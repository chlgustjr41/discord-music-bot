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

## Web app: dashboard presence

The session dashboard (`/dashboard/:sessionCode`) is multi-user by construction — everyone with the code reads one Firestore document, so queue and playback are shared whether or not anyone signs in. On top of that sits one small collaborative feature: **an avatar row showing who is looking at this dashboard right now.**

Live cursors and shared view state (synced panel expand/collapse and text inputs) were built and then **deliberately removed** — on a screen whose job is controlling music they were noise, and the shared/solo toggle existed only to switch that noise off. Presence alone is what earned its keep. Their removal also took a whole write surface with it, including the impersonation and force-collapse holes that surface had needed rules to close.

**Where it lives.** `presence/{sessionCode}/participants/{uid}` — a **top-level collection**, deliberately not under `servers/`. Firestore rules are OR'd across matches, and `servers/{id}/{subcollection}/{doc}` is `allow read, write: if true`; anything placed there could never be restricted however carefully its own rule was written.

**Read is public; write is uid-scoped.** The session code is already the capability for queue, playback and history, so who is looking is no more sensitive than what is playing — and a signed-out visitor is meant to see the row. You can watch without signing in; you can only put *yourself* on the list, and only you can refresh or remove you. Appearing requires a uid, which is what the write rule is built on, so anonymous visitors see the bar without joining it.

**Why the server stamps the timestamp.** A client-supplied `updatedAt` lets any signed-in user write one far-future value and stay in everyone's avatar row forever — and because writes are uid-scoped, nobody else could delete them. Clock skew alone would make honest users flicker in and out. The client keeps a two-sided sanity window (`|age| <= TTL`), because `updatedAt` is the server's clock while `now` is the browser's; a one-sided clamp makes any viewer with a slow clock see nobody at all.

**Liveness and focus.** Firestore has no server-side disconnect hook, so a 15 s heartbeat plus a 45 s staleness filter — not `onDisconnect` — is what removes someone whose laptop lid closed. Documents are also deleted on unload. A `focused` boolean (visible tab **and** focused window) greys out people who are present but not looking; it is written only when the value actually changes, since alt-tab fires several events per switch.

**Everyone appears, signed in or not.** A signed-out visitor publishes under `anon_<browserId>` — the same stable per-browser id the leaderboard uses — and shows their nickname, or **"Anonymous N"** if they have not set one. The number is assigned at render by sorting nameless anonymous rows by document id, so every viewer independently arrives at the same numbering; it is not join order, because `updatedAt` moves on every heartbeat and a write-once `joinedAt` would be clobbered by the merge writes. Numbering shifts when someone leaves — accepted, not hidden.

The rules split on the document id: a row whose id is `request.auth.uid` is uid-scoped as before, and a row matching `anon_[A-Za-z0-9_-]{8,64}` is writable **without auth**. The consequence is real and accepted: anyone with the session code can create, overwrite or delete an anonymous row. It is the trust level this app already runs at — `servers/{id}` and every subcollection under it are `allow read, write: if true`, so the queue is strictly more valuable and already open — and signed-in rows keep their scoping, so an account's avatar still cannot be forged.

**Why a pending write must not blink you out.** Every heartbeat writes `updatedAt: serverTimestamp()`, and Firestore fires a *local* snapshot first with that field still `null`. Turning that into `NaN` and filtering it — correct for a malformed row — made your own avatar vanish for each round trip, about every 15 seconds. The fix is not to weaken the guard but to use `metadata.hasPendingWrites`: a document this browser is mid-write on is the one row whose liveness is not in question. An unresolved timestamp with **no** pending write is still filtered, so "unresolved" never comes to mean "fresh".

**Names.** `identity.ts` resolves `nickname || accountName || "Web User"` — nickname first, so a signed-in user can rename themselves by clicking their badge, and presence republishes immediately. `photoURL` is restricted to `*.googleusercontent.com` in both the rules and the component: it is an `<img src>` rendered for every viewer, so an arbitrary host would be an IP/User-Agent beacon.

**Leaderboard identity.** Per-member stats (`servers/{id}/memberStats`) are keyed on a **stable id** — the account uid, or a random per-browser id for someone who has only set a nickname — never on the display name. Name-keying meant renaming yourself started a fresh row and left the old one frozen beside it in the leaderboard, and two people who happened to share a display name silently shared a row. The name still travels with the document so the leaderboard can render it; it is data, not identity. A one-time transactional migration folds a legacy name-keyed row into the stable-keyed one on first write, so nobody appears twice.

**Idle sign-out** is app-wide (`src/lib/idleSignOut.ts`): 30 minutes, warning at 60 s, cross-tab via `localStorage`, timestamp-based so a sleeping laptop signs out on wake rather than resuming a stale timer.
