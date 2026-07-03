# Architecture

> Living document. Derived from the approved design spec
> (`docs/superpowers/specs/2026-07-02-stability-rewrite-design.md`);
> update THIS file as the system evolves — the spec stays frozen.

## Governing principle: crash-only

Every container may be killed at any instant; the system converges back to
correct state because durable state lives outside containers (Firestore +
token volume). Recovery is always "restart," never choreography.

## System overview

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

### `bot` (Python 3.11, discord.py)

- Discord slash commands, voice connection, playback orchestration. Continues to honor the existing Firestore command documents written by the web dashboard.
- Talks to Lavalink through a **thin owned client** (~300–400 lines: REST for track loading, WebSocket for events) replacing wavelink. We own reconnect behavior; no monkey-patching.
- **Stateless:** on startup, rebuilds all player state from Firestore and re-attaches to Lavalink's session (Lavalink v4 session resuming lets a restarted bot adopt still-playing players).
- Contains **zero** watchdog/recovery code — that is the guardian's job.
- All node access goes through a `NodeProvider` interface. v1 ships one implementation (the VM's Lavalink). The future local-node feature is a second implementation with automatic fallback to the VM node when a local node disconnects — no rewrite required.

### `lavalink` (Lavalink v4 + youtube-source plugin)

- The audio engine. Client order tuned for datacenter IPs (`MUSIC` search-only, `TVHTML5_SIMPLY` first for playback, then `WEB`, `WEBEMBEDDED`, `ANDROID_VR`, `TV` carrying OAuth).
- The plugin version is declared in **exactly one place** (`.env`) and templated into `application.yml` at container start (`application.yml.tmpl` + `entrypoint.sh`), making version drift structurally impossible.
- OAuth refresh token supplied via env var.

### `token-minter` (scheduled one-shot)

- Every ~6 hours: starts, runs headless Chromium against YouTube (trusted-session-generator approach), harvests fresh `poToken` + `visitorData`, pushes them to Lavalink **at runtime** via the youtube-source plugin's REST endpoint (no restart), writes them to the shared volume for cold starts, exits.
- No Google account involved → nothing revocable. Independent of the OAuth layer.

### `guardian` (Python service, ~400 lines)

The supervisor, outside every failure domain it watches. Four duties, one module each:

1. **Probe** — every 2 min: a canary track lookup against Lavalink REST + a health ping to the bot; also compares Lavalink player position between probes when state says "playing" (frozen-position = silent failure).
2. **Classify** — maps failure signatures to playbook IDs F1–F9 (see the [Runbook](../operations/RUNBOOK.md)).
3. **Act** — restarts sick containers via the Docker socket; triggers an immediate token-minter run on poToken rejection.
4. **Alert** — Discord webhook to the admin channel with the playbook ID, diagnosis, and exact fix command when a human is required. Daily youtube-source GitHub release check (drift warning before breakage). Weekly heartbeat message proving the alert channel itself works.

## Data Flow

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
