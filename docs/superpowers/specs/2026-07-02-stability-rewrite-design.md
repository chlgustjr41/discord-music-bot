# Jacky Music — Stability Rewrite Design

**Date:** 2026-07-02
**Status:** Approved pending final user review
**Scope:** Bot + audio core rewrite. The React dashboard (`frontend/`), Cloud Functions (`functions/`), and the existing Firestore schema are kept and integrated with, not rewritten.

## 1. Problem

The bot's playback goes down too often, and outages last for days because they fail silently. Root-cause analysis of past incidents identified two failure classes:

**Class A — YouTube source failures (dominant):**
- Google periodically revokes the OAuth refresh token → every track load fails with "requires login" until a human re-authenticates
- YouTube changes its player JS every ~1–2 weeks, breaking signature extraction in stale `youtube-source` plugin versions; the plugin jar and the `application.yml` declaration have drifted out of sync before, killing playback
- GCP datacenter IPs are flagged by YouTube's bot detection, walling off WEB-family clients

**Class B — connection/state failures:**
- Silent playback failures after Lavalink restarts; wavelink's connection-state model required monkey-patching (DAVE support) and made recovery hard
- Recovery logic accreted inside the bot (`playback.py` reached 1,443 lines of interleaved playback and watchdog code)

## 2. Goals

1. **YouTube source resilience (#1 goal):** layered, independent auth strategies so no single revocation kills playback; failures detected in minutes, classified automatically, auto-fixed where possible, and alerted with the exact fix when a human is required. Target MTTR: minutes, not days.
2. **Crash-only architecture:** every container can be killed at any instant and the system converges back to correct state. Recovery is always "restart," never choreography.
3. **Host agnosticism:** deploy contract is `git clone` → fill `.env` → `docker compose up -d`. Build/test on the current GCP e2-small VM; migrate to Hetzner when the account is active. No cloud-specific runtime dependencies.
4. **Enterprise-readable repo:** service-per-directory structure, ADRs for decisions, an operator runbook keyed to alert IDs, issue/PR-driven development with CI gates.

**Non-goals (v1):** local (user-hosted) Lavalink nodes — designed for via the `NodeProvider` interface, implemented later. Dashboard features, search-engine improvements, Spotify — see §10 roadmap.

**Constraints:** 2GB RAM VM (~$13/mo budget). Memory budget: OS ~300MB + Lavalink ~800MB RSS + bot ~150MB + guardian ~50MB ≈ 1.3GB steady state; token-minter spikes ~400MB briefly as a scheduled one-shot.

## 3. Architecture

One VM, four Docker Compose services. State lives outside containers: Firestore (queues, player state, guild config) and a named volume (tokens).

```
                        Discord                    YouTube
                           ▲                          ▲
                           │ gateway/voice            │ (poToken + OAuth + client-order)
┌──────────────────────────┼──────────────────────────┼─────────────────────┐
│ VM · Docker Compose      │                          │                     │
│                      ┌───┴───┐   REST/WS      ┌─────┴─────┐               │
│                      │  bot  ├───────────────►│ lavalink  │               │
│                      └───┬───┘                └─▲───────▲─┘               │
│                          │ health ping          │       │ poToken push    │
│                      ┌───▼───────┐ canary lookup│  ┌────┴─────────┐       │
│                      │ guardian  ├──────────────┘  │ token-minter │       │
│                      └───┬───────┘                 │ (scheduled   │       │
│                          │ restart / alert         │  one-shot)   │       │
│                          ▼                         └──────────────┘       │
│                    Docker API + Discord webhook                           │
└───────────────────────────────────────────────────────────────────────────┘
```

### 3.1 `bot` (Python 3.11, discord.py)

- Discord slash commands, voice connection, playback orchestration. Continues to honor the existing Firestore command documents written by the web dashboard.
- Talks to Lavalink through a **thin owned client** (~300–400 lines: REST for track loading, WebSocket for events) replacing wavelink. We own reconnect behavior; no monkey-patching.
- **Stateless:** on startup, rebuilds all player state from Firestore and re-attaches to Lavalink's session (Lavalink v4 session resuming lets a restarted bot adopt still-playing players).
- Contains **zero** watchdog/recovery code — that is the guardian's job.
- All node access goes through a `NodeProvider` interface. v1 ships one implementation (the VM's Lavalink). The future local-node feature is a second implementation with automatic fallback to the VM node when a local node disconnects — no rewrite required.

### 3.2 `lavalink` (Lavalink v4 + youtube-source plugin)

- The audio engine. Client order tuned for datacenter IPs (`MUSIC` search-only, `TVHTML5_SIMPLY` first for playback, then `WEB`, `WEBEMBEDDED`, `ANDROID_VR`, `TV` carrying OAuth).
- The plugin version is declared in **exactly one place** (`.env`) and templated into `application.yml` at container start (`application.yml.tmpl` + `entrypoint.sh`), making version drift structurally impossible.
- OAuth refresh token supplied via env var.

### 3.3 `token-minter` (scheduled one-shot)

- Every ~6 hours: starts, runs headless Chromium against YouTube (trusted-session-generator approach), harvests fresh `poToken` + `visitorData`, pushes them to Lavalink **at runtime** via the youtube-source plugin's REST endpoint (no restart), writes them to the shared volume for cold starts, exits.
- No Google account involved → nothing revocable. Independent of the OAuth layer.

### 3.4 `guardian` (Python service, ~400 lines)

The supervisor, outside every failure domain it watches. Four duties, one module each:

1. **Probe** — every 2 min: a canary track lookup against Lavalink REST + a health ping to the bot; also compares Lavalink player position between probes when state says "playing" (frozen-position = silent failure).
2. **Classify** — maps failure signatures to playbook IDs F1–F9 (§5).
3. **Act** — restarts sick containers via the Docker socket; triggers an immediate token-minter run on poToken rejection.
4. **Alert** — Discord webhook to the admin channel with the playbook ID, diagnosis, and exact fix command when a human is required. Daily youtube-source GitHub release check (drift warning before breakage). Weekly heartbeat message proving the alert channel itself works.

## 4. Data Flow

**Playback (happy path):**
1. `/play` (or a dashboard command doc) → bot resolves the request
2. Bot asks Lavalink to load the track; youtube-source hits YouTube carrying poToken + OAuth + client ordering
3. Bot writes queue/player state to **Firestore first**, then instructs Lavalink to play — Firestore is the source of truth; containers hold only caches
4. Track-end events arrive over the owned WebSocket → bot pops the next track from Firestore

**Recovery (any container dies):**
- `bot` restarts → reads Firestore + re-attaches Lavalink session → converges; audio continues during the gap via session resuming
- `lavalink` restarts → guardian detects; bot's client reconnects with backoff and re-issues "play at position X" from Firestore → seconds of gap, then resumes
- `guardian` restarts → Docker `restart: unless-stopped`; it is stateless
- `token-minter` fails → previous token remains valid (token validity windows overlap)

## 5. Failure Playbook

Every alert the guardian sends carries its ID; the runbook (`docs/operations/RUNBOOK.md`) documents each ID with diagnosis and exact commands.

| ID | Failure | Detected by | Automated response | Human needed? |
|----|---------|-------------|--------------------|---------------|
| F1 | poToken stale/rejected | Canary: bot-detection error | Trigger token-minter immediately, re-probe | No |
| F2 | OAuth token revoked | Canary: "requires login" + OAuth 400 | Alert with one-command re-auth (`make reauth`) | **Yes** (~60s device flow) |
| F3 | Plugin broken by YouTube JS change | Canary: signature/cipher errors | Alert with exact version bump; release watcher usually warns first | Yes (approve bump) |
| F4 | Lavalink sick/dead | Canary timeout / Docker health | Guardian restarts container; bot reconnects + restores from Firestore | No |
| F5 | Bot hung (gateway zombie) | Guardian's bot health ping | Guardian restarts bot container | No |
| F6 | Silent playback (position frozen) | Position comparison across probes | Restart playback via bot; escalate to container restart on repeat | No |
| F7 | VM down / Docker dead | External uptime monitor (free tier) pinging guardian's heartbeat URL | External email/DM | Yes |
| F8 | Firestore unreachable | Bot + guardian error rates | Continue from in-memory cache; queue writes, flush on recovery; alert if sustained | No |
| F9 | Alert channel broken | Weekly guardian heartbeat message | — | Missing heartbeat noticed by operator |

Design rationale: F2 (the historical multi-day outage) floors at "human runs one command minutes after a DM," and F1's poToken layer — with no account to revoke — carries playback while F2 is pending. The layers fail independently.

## 6. Repository Structure

`frontend/` and `functions/` remain untouched. The old `bot/` is replaced; logic is ported selectively, not copied.

```
discord-music-bot/
├── README.md                     # What it is, architecture diagram, quickstart, doc map
├── Makefile                      # THE command interface: make up/dev/test/deploy/reauth/logs
├── docs/
│   ├── architecture/
│   │   ├── ARCHITECTURE.md       # System overview, diagrams, crash-only principles
│   │   └── decisions/            # ADRs: 0001-owned-lavalink-client.md,
│   │                             #   0002-potoken-sidecar.md, 0003-crash-only-state.md, …
│   ├── operations/
│   │   ├── RUNBOOK.md            # Playbook F1–F9 as an operator manual
│   │   └── DEPLOYMENT.md         # GCP now / Hetzner later; both "clone + .env + compose up"
│   └── roadmap/
│       └── FUTURE.md             # Deferred features (§10)
├── services/
│   ├── bot/
│   │   ├── src/jacky/
│   │   │   ├── commands/         # Slash commands & Discord-facing handlers (thin)
│   │   │   ├── audio/            # Owned Lavalink client, NodeProvider interface
│   │   │   ├── state/            # Firestore repositories (queue, player, guild config)
│   │   │   └── core/             # Config, logging, lifecycle
│   │   ├── tests/
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   ├── guardian/
│   │   ├── src/guardian/         # probe.py, classify.py, act.py, alert.py
│   │   ├── tests/
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   ├── lavalink/
│   │   ├── application.yml.tmpl  # Plugin version templated from .env
│   │   └── entrypoint.sh
│   └── token-minter/
│       └── Dockerfile            # Trusted-session-generator + push-to-Lavalink step
├── deploy/
│   ├── docker-compose.yml
│   └── .env.example              # Every variable documented inline
└── .github/
    ├── workflows/ci.yml
    ├── ISSUE_TEMPLATE/           # feature.md, bug.md, ops-incident.md
    └── PULL_REQUEST_TEMPLATE.md
```

**Documentation standards:** every service README answers *what it does / how to run it / what it depends on*. ADRs record why, not just what. Guardian alerts link to runbook sections by ID. The Makefile is the single human interface.

## 7. Development Workflow

- **Issues first:** the implementation plan becomes GitHub issues grouped under milestones — M1 Foundation (structure, CI, compose skeleton), M2 Audio infrastructure (Lavalink config, token-minter), M3 Bot core (owned client, state, commands), M4 Guardian, M5 Production cutover. Labels: `service:bot`, `service:guardian`, `type:feature`, `type:ops`.
- **Branching:** `master` is protected; work happens on `feat/<issue#>-short-name` branches.
- **PRs:** every unit of work is a PR referencing its issue (`Closes #N`). Template enforces: what/why, test evidence, docs updated, runbook updated if failure behavior changed. The owner reviews and merges every PR.
- **CI on every PR:** ruff + pytest (both services) + `docker compose config` validation + Docker image builds. Red CI blocks merge.

## 8. Testing

- **Unit (pytest, CI):** guardian classifier (error text → F1–F9 — exhaustively tested; it is the brain), state repositories against the Firestore emulator, Lavalink client protocol handling against a fake WebSocket.
- **Integration (compose-based, CI):** real Lavalink + fake bot; verify canary round-trip, token push endpoint, guardian restart action against a deliberately killed container.
- **End-to-end (manual, per release):** runbook checklist — real Discord server, `/play`, kill Lavalink mid-track and observe recovery, simulate F2 and confirm the DM arrives.
- **Continuous:** the canary probe is production testing every 2 minutes, forever.

## 9. Deployment

- **Now:** GCP e2-small (`personal-project-machine`, project `personal-server-492701`). Deploy = `git pull && docker compose up -d --build` (wrapped as `make deploy`).
- **Later:** Hetzner (~€4/mo, better IP reputation tier). Migration = clone + `.env` + `docker compose up -d`; an external uptime monitor repointed. No code changes.
- Secrets live in `.env` on the host (never committed); `.env.example` documents every variable.

## 10. Future Roadmap (out of scope, brainstorm later)

1. **Local audio nodes** — user-hosted Lavalink for low latency; `NodeProvider` second implementation with automatic fallback to the VM node on local-node disconnect.
2. **Dashboard "summon"** — logged-in users click a previously-visited voice channel in the web UI to make the bot join it (within allowed guilds).
3. **Search engine fix** — playlist URL → the URL's track plus the playlist's tracks as results; single-video URL → normal search with similar recommendations.
4. **Spotify support.**
