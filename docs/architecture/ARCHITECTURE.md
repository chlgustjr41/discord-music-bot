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

## Web app: collaborative dashboard

The session dashboard (`/dashboard/:sessionCode`) is multi-user by construction — everyone with the code reads one Firestore document, so queue and playback are shared whether or not anyone signs in. Layered on top of that is an **opt-in shared view** for signed-in users: who else is here, and where their pointer is.

**Where presence lives.** `presence/{sessionCode}/participants/{uid}` — a **new top-level collection**, deliberately not under `servers/`. Firestore rules are OR'd across matches, and `servers/{id}/{subcollection}/{doc}` is `allow read, write: if true`; anything placed there could never be restricted no matter how its own rule was written. At the top level the rules mean something: read requires auth, writes are uid-scoped, the document shape is constrained, and `updatedAt` must equal `request.time`.

**Why the server stamps the timestamp.** A client-supplied `updatedAt` lets any signed-in user write one far-future value and remain in everyone's presence bar forever — and because writes are uid-scoped, nobody else can delete them. Clock skew alone would also make honest users flicker in and out. The client keeps a two-sided sanity window (`|age| <= TTL`) because `updatedAt` is the server's clock while `now` is the browser's.

**Liveness.** Firestore has no server-side disconnect hook, so a heartbeat every 15 s plus a 45 s staleness filter — not `onDisconnect` — is what removes someone whose laptop lid closed. Docs are also deleted on unload and on switching to solo. Crashed clients leave a document behind that nothing sweeps; a Firestore TTL policy on `updatedAt` would clean those up if it ever matters.

**Modes.**

| | Anonymous | Signed in, solo | Signed in, shared |
|---|---|---|---|
| Publishes presence/cursor | never | no | yes |
| Sees others | no | no | yes |
| Follows others' searches | n/a | no | yes |

Solo is symmetric on purpose: opting out of being watched also stops you watching. `shouldPublish(mode, signedIn)` in `src/lib/presence.ts` is the single gate, so the auth check cannot be applied on one path and forgotten on another.

**Cost.** Cursor writes are throttled to 100 ms, suppressed below 8 px of movement, and skipped when the tab is hidden — bursty ~3 writes/s per active user. Presence updates re-render only `PresenceLayer`, not the dashboard panels, which is why `usePresence` lives there rather than in `Dashboard`.

**Search is a bot capability, and that limits solo mode.** `SearchPanel` writes `searchQuery` to `servers/{id}` and the *bot* writes results back, so a shared-mode search is visible to everyone by construction. Solo mode therefore (a) stops following other people's searches and (b) tries a client-side endpoint first, falling back to the bot when it is unavailable. `functions/searchYouTube` is **not deployed** today, so that fallback is the live path — solo search works, it is just not yet private, and the panel toasts once to say so. Deploying the function with a `YOUTUBE_API_KEY` makes solo search private with no frontend change; the `/api/searchYouTube` hosting rewrite is already in place.

**Idle sign-out** is app-wide (`src/lib/idleSignOut.ts`): 30 minutes, warning at 60 s, cross-tab via `localStorage`, timestamp-based so a sleeping laptop signs out on wake rather than resuming a stale timer.
