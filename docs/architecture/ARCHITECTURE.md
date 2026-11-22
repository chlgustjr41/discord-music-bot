# Architecture

> Living document. Derived from the approved design spec
> (`docs/superpowers/specs/2026-07-02-stability-rewrite-design.md`);
> update THIS file as the system evolves — the spec stays frozen.

## Governing principle: crash-only

Every container may be killed at any instant; the system converges back to
correct state because durable state lives outside containers (Firestore +
token volume). Recovery is always "restart," never choreography.

## System overview

One host running Docker Compose, plus three cloud services that are not ours
to run: Discord, Firestore/Firebase Hosting, and Cloudflare. Durable state
lives outside the containers, in Firestore (queues, player state, playlists,
session codes, control tokens) and one small named volume (the current
YouTube token).

```
   people                         cloud                              the host (Docker Compose)
 ───────────                 ───────────────          ┌──────────────────────────────────────────────────┐
 Discord chat  ─────────────► Discord ◄──── gateway + voice (outbound) ────┐                              │
 "j!play ..."                                         │                    │                              │
                                                      │   ┌────────────────┴───┐  REST + WebSocket  ┌─────┴──────┐
 Web dashboard ─────────────► Firestore ◄─────────────┼───┤        bot         ├───────────────────►│  lavalink  ├──► YouTube
 (session code)               (shared state)          │   └─┬──────────────▲───┘                    └─▲───────▲──┘   SoundCloud
                                                      │     │ /health      │ /control/*               │       │
 Stream Deck / ─► Cloudflare ◄──── tunnel (outbound) ─┼─────┼──── cloudflared                  cipher ┘       │ token push
 Creative Console  (HTTPS)                            │     │                                                  │
                                                      │   ┌─▼─────────┐   list / restart only   ┌──────────────┴─┐
                                                      │   │ guardian  ├──► socket-proxy ──► Docker   token-minter ├──► pot-provider
                                                      │   └─────┬─────┘                         └────────────────┘
                                                      └─────────┼────────────────────────────────────────────────┘
                                                                ▼
                                                   Discord webhook (alerts to the operator)
```

Nothing listens for inbound connections. The bot dials Discord, the tunnel
dials Cloudflare, and the dashboard never talks to the host at all: it writes
to Firestore and the bot reacts.

### What each part is for

| Part | What it is for | The problem it solves |
|------|----------------|------------------------|
| **bot** (Python, discord.py) | Turns requests from three places (Discord commands, dashboard writes in Firestore, the control API) into one queue, and tells the audio server what to play. | Without a single coordinator the three front ends would disagree about what is playing. It keeps no state of its own, so a restart rebuilds everything from Firestore. |
| **lavalink** (Java) | The audio server. Fetches the stream, decodes and re-encodes it, and sends Discord one audio packet every 20 ms. | Real-time audio is timing-sensitive and CPU-heavy. Keeping it out of the bot means the bot can restart without stopping the music, and a stall in one does not freeze the other. |
| **youtube-source** (plugin inside lavalink) | Knows how to obtain a playable stream from YouTube. | YouTube offers no playback API and changes its site often. This is the part that breaks most, so its version is declared in exactly one place (`.env`). |
| **cipher** (yt-cipher sidecar) | Runs YouTube's own player JavaScript to compute the signatures that stream URLs require. | Hand-written signature extraction broke on every YouTube player update (the July 2026 outages). Running the real code survives those updates. |
| **pot-provider** (bgutil sidecar) | Produces "proof of origin" tokens by solving the challenge YouTube gives real browsers. | YouTube refuses or throttles clients that cannot show one, especially from server addresses. |
| **token-minter** | Every 5.5 hours asks pot-provider for a fresh token, pushes it into the running Lavalink, and saves it to a volume for cold starts. | Tokens expire after about six hours. Without a scheduler, playback would stop a few hours after every start. |
| **guardian** | A watchdog outside the things it watches. Every two minutes it checks that Lavalink can find a track, that the bot answers, and that a "playing" position is really moving. It classifies failures (playbooks F1 to F9), restarts what a restart can fix, and alerts the operator with the fix when a human is needed. | A hung process cannot report that it is hung, and failures happen when nobody is looking. |
| **socket-proxy** | Sits between guardian and Docker and permits exactly two requests: list containers, restart a container. | Restart ability normally means full control of Docker, which is root on the host. On a home server the host holds personal files, so guardian gets only the two calls it uses. |
| **cloudflared** | Keeps an outbound tunnel to Cloudflare, through which `https://control.<domain>/control/*` reaches the bot. | The Stream Deck and Creative Console can reach the bot from anywhere without a forwarded port, and without the host's address being visible. |
| **Firestore** (cloud) | The single source of truth, shared by the bot and the dashboard. | The dashboard and the bot never need a direct connection, and any container can die without losing the queue. |
| **Web dashboard** (React on Firebase Hosting) | Shows and controls a session for anyone who has its code. "My Servers" and activation use Google sign-in. | Gives people a queue and controls without Discord commands, with nothing to install and no account for guests. |
| **Hardware plugins** | Physical keys for play/pause, skip, volume, summon, playlists, "post to Discord", and a voice-command key. They sign in with Discord and call the control API. | Control without switching windows. The voice key sends a short recording to the bot, which transcribes it and may only run commands from a fixed list. |
| **Host firewall rule** (`deploy/nas/egress-firewall.sh`) | Lets the containers reach the internet and each other, and nothing on a private address. | The stack fetches media on behalf of anyone who can queue a track. On a home network that must never include the router or other devices. |

### How the parts are kept apart

- **No published ports.** Every service is reachable only on its compose
  networks. See `docs/operations/DEPLOYMENT.md` for which service shares a
  network with which.
- **Least privilege.** Every container drops all Linux capabilities and cannot
  gain new privileges. The bot, guardian and token-minter run as an
  unprivileged user on a read-only filesystem. Each service has a memory
  limit taken from measured use.
- **Only known media sources.** Lavalink's generic http source is off, and the
  bot refuses any link that is not YouTube or SoundCloud before it reaches
  Lavalink. The two lists must change together
  (`services/lavalink/application.yml.tmpl`, `services/bot/src/jacky/audio/models.py`).

### `bot`

- Discord commands, voice connection, playback orchestration, the control API
  (`/control/*`), and the listener that reacts to dashboard writes.
- Talks to Lavalink through a **thin owned client** (REST for track loading,
  WebSocket for events). We own reconnect behavior; no monkey-patching.
- **Stateless:** on startup it rebuilds all player state from Firestore and
  re-attaches to Lavalink's session (session resuming lets a restarted bot
  adopt still-playing players).
- Contains **zero** watchdog/recovery code — that is the guardian's job.
- Records the audio server's per-minute frame statistics (frames sent, frames
  with no audio ready, frames not sent on time) and reports them in `/health`
  and `j!status`, so audio-quality problems can be traced to the source or to
  the sender.

### `lavalink` (Lavalink v4 + youtube-source plugin)

- Client order is tuned for hard-to-please addresses (`MUSIC` search-only,
  `TVHTML5_SIMPLY` first for playback, then `WEB`, `WEBEMBEDDED`,
  `ANDROID_VR`, `TV` carrying OAuth).
- The plugin version is declared in **exactly one place** (`.env`) and
  templated into `application.yml` at container start, and the jar's checksum
  is verified on every boot.
- The garbage collector is pinned and the send queue and frame buffer are
  configurable (`LAVALINK_BUFFER_MS`, `LAVALINK_FRAME_BUFFER_MS`), because
  audio pacing is what listeners hear as speeding up and slowing down.

### `guardian`

The supervisor, outside every failure domain it watches. Four duties, one
module each:

1. **Probe** — every 2 min: a canary track lookup against Lavalink REST + a
   health ping to the bot; also compares Lavalink player position between
   probes when state says "playing" (frozen position = silent failure).
2. **Classify** — maps failure signatures to playbook IDs F1–F9 (see the
   [Runbook](../operations/RUNBOOK.md)).
3. **Act** — restarts sick containers through the socket proxy, with a
   ten-minute cooldown per service; triggers an immediate token-minter run on
   poToken rejection.
4. **Alert** — Discord webhook to the admin channel with the playbook ID,
   diagnosis, and exact fix command when a human is required. Daily
   youtube-source release check. Weekly heartbeat proving the alert channel
   itself works.

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

**Where it lives.** `presence/{sessionCode}/participants/{uid}` — a **top-level collection**, deliberately not under `servers/`. It was placed there when `servers/{id}` had a wildcard subcollection rule that allowed everything: Firestore rules are OR'd across matches, so nothing under `servers/` could have been restricted then. That wildcard is gone (each subcollection is now named, with its own rule), but presence keeps its own top-level home.

**Read is public; write is uid-scoped.** The session code is already the capability for queue, playback and history, so who is looking is no more sensitive than what is playing — and a signed-out visitor is meant to see the row. You can watch without signing in; you can only put *yourself* on the list, and only you can refresh or remove you. Appearing requires a uid, which is what the write rule is built on, so anonymous visitors see the bar without joining it.

**Why the server stamps the timestamp.** A client-supplied `updatedAt` lets any signed-in user write one far-future value and stay in everyone's avatar row forever — and because writes are uid-scoped, nobody else could delete them. Clock skew alone would make honest users flicker in and out. The client keeps a two-sided sanity window (`|age| <= TTL`), because `updatedAt` is the server's clock while `now` is the browser's; a one-sided clamp makes any viewer with a slow clock see nobody at all.

**Liveness and focus.** Firestore has no server-side disconnect hook, so a 15 s heartbeat plus a 45 s staleness filter — not `onDisconnect` — is what removes someone whose laptop lid closed. Documents are also deleted on unload. A `focused` boolean (visible tab **and** focused window) greys out people who are present but not looking; it is written only when the value actually changes, since alt-tab fires several events per switch.

**Everyone appears, signed in or not.** A signed-out visitor publishes under `anon_<browserId>` — the same stable per-browser id the leaderboard uses — and shows their nickname, or **"Anonymous N"** if they have not set one. The number is assigned at render by sorting nameless anonymous rows by document id, so every viewer independently arrives at the same numbering; it is not join order, because `updatedAt` moves on every heartbeat and a write-once `joinedAt` would be clobbered by the merge writes. Numbering shifts when someone leaves — accepted, not hidden.

The rules split on the document id: a row whose id is `request.auth.uid` is uid-scoped as before, and a row matching `anon_[A-Za-z0-9_-]{8,64}` is writable **without auth**. The consequence is real and accepted: anyone with the session code can create, overwrite or delete an anonymous row. It is the trust level this app already runs at — anyone holding the session code may change the queue and the playback state, which are strictly more valuable — and signed-in rows keep their scoping, so an account's avatar still cannot be forged.

**Why a pending write must not blink you out.** Every heartbeat writes `updatedAt: serverTimestamp()`, and Firestore fires a *local* snapshot first with that field still `null`. Turning that into `NaN` and filtering it — correct for a malformed row — made your own avatar vanish for each round trip, about every 15 seconds. The fix is not to weaken the guard but to use `metadata.hasPendingWrites`: a document this browser is mid-write on is the one row whose liveness is not in question. An unresolved timestamp with **no** pending write is still filtered, so "unresolved" never comes to mean "fresh".

**Names.** `identity.ts` resolves `nickname || accountName || "Web User"` — nickname first, so a signed-in user can rename themselves by clicking their badge, and presence republishes immediately. `photoURL` is restricted to `*.googleusercontent.com` in both the rules and the component: it is an `<img src>` rendered for every viewer, so an arbitrary host would be an IP/User-Agent beacon.

**Leaderboard identity.** Per-member stats (`servers/{id}/memberStats`) are keyed on a **stable id** — the account uid, or a random per-browser id for someone who has only set a nickname — never on the display name. Name-keying meant renaming yourself started a fresh row and left the old one frozen beside it in the leaderboard, and two people who happened to share a display name silently shared a row. The name still travels with the document so the leaderboard can render it; it is data, not identity. A one-time transactional migration folds a legacy name-keyed row into the stable-keyed one on first write, so nobody appears twice.

**Idle sign-out** is app-wide (`src/lib/idleSignOut.ts`): 30 minutes, warning at 60 s, cross-tab via `localStorage`, timestamp-based so a sleeping laptop signs out on wake rather than resuming a stale timer.
