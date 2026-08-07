# Stream Deck Session Control — Design

**Date:** 2026-08-06
**Status:** Approved
**Scope:** New `streamdeck-plugin/` top-level directory (Node.js/TypeScript, official Elgato SDK), a new Control API on the v2 bot (`services/bot/`), and a `cloudflared` sidecar in `deploy/docker-compose.yml`. Frontend, Firestore contract, guardian, and Lavalink untouched.

## Problem

Controlling the bot today requires Discord (`j!` commands) or the web
dashboard (Firestore writes). The user wants physical Stream Deck keys —
play/pause, skip, stop, volume, now-playing — that control **their current
session**: whichever guild they are presently sitting in a voice channel
with an active bot session. It must work from anywhere (not just the home
LAN), and the plugin is for personal use — packaged as a
`.streamDeckPlugin` file installed locally, not published to the Elgato
Marketplace.

## Decisions

| Question | Decision |
|---|---|
| Control transport | **New authenticated REST API on the bot** (Approach A). Rejected: writing Firestore like the web app (needs a Firebase credential in the plugin, couples to a schema still being reworked); puppeting Discord commands (ToS / scraping issues). |
| Session identification | **Discord voice-state based.** The plugin sends the user's Discord user ID; the bot scans its guilds via its existing Gateway state for the guild where that user is currently in a voice channel *and* a `PlayerService` session is active. No Discord OAuth, no Google account flow. |
| Where the Discord user ID lives | In the **plugin's settings** (Property Inspector, stored as Stream Deck global settings), alongside API URL and token — not hardcoded in bot env. |
| Auth | Static bearer token: `CONTROL_API_TOKEN` env var on the bot, constant-time compare. Rotatable by changing the env var. |
| Internet exposure | **Cloudflare named tunnel** (`cloudflared` sidecar in docker-compose) → `control.<domain>`. No inbound firewall ports opened on the GCP VM. A named tunnel (not a quick tunnel) so the URL is stable across restarts. |
| Plugin runtime | Official `@elgato/streamdeck` SDK, Node.js ≥ 24, TypeScript, scaffolded with `streamdeck create`. Requires Stream Deck app ≥ 7.1. |
| Distribution | `streamdeck pack` → `.streamDeckPlugin` file, double-click local install. No Marketplace submission. |
| Volume | Two key actions (+5 / −5). Stream Deck+ dial support out of scope for v1. |

## Prerequisite

A domain managed in a Cloudflare account (free plan is fine) to host the
named tunnel's hostname. If none exists yet, registering one (or moving an
existing domain's DNS to Cloudflare) happens before the deploy step.

## Components

### 1. Bot Control API — `services/bot/src/jacky/api/control.py` (new)

Routes mounted on the same aiohttp app that serves `core/health.py`
(port 8080, container-internal; the tunnel sidecar reaches it over the
compose network, so it stays unpublished on the host):

| Route | Behavior |
|---|---|
| `POST /control/play-pause` | Toggle pause on the resolved session. |
| `POST /control/skip` | Skip current track. |
| `POST /control/stop` | End playback (existing stop semantics — same as `j!stop`). |
| `POST /control/volume` | Body `{"delta": int}`; clamps to the bot's valid volume range. |
| `GET /control/now-playing` | `{"active": true, "title", "author", "paused", "volume", "guildName"}` or `{"active": false}`. |

- All routes require `Authorization: Bearer <CONTROL_API_TOKEN>`
  (`hmac.compare_digest`); 401 otherwise. If `CONTROL_API_TOKEN` is unset,
  the control routes are not registered at all (health endpoint unaffected).
- All routes take the caller's Discord user ID (query param on GET, JSON
  body field on POST): `discordUserId`.
- **Session resolution:** iterate `bot.guilds`; the target is the guild
  where `guild.get_member(discordUserId)` has non-null
  `member.voice.channel` and the guild has an active `PlayerService`
  session. First match wins. No match → `now-playing` returns
  `{"active": false}` (200); action routes return 409.
- Action handlers are thin wrappers over existing `PlayerService` methods
  (`pause`/`resume`, `skip`, `stop`, `set_volume`) — the same call surface
  `commands/playback.py` and `state/listener.py` use, so Firestore state
  stays consistent however playback is driven.

### 2. Deploy — `deploy/docker-compose.yml` + `.env`

- New `cloudflared` service (`cloudflare/cloudflared` image, `tunnel run`,
  token via `CLOUDFLARE_TUNNEL_TOKEN` env var), on the compose network,
  routing `control.<domain>` → `http://bot:8080`.
- The tunnel's ingress is configured to forward only `/control/*` (and
  nothing else) so `/health` stays private.
- New env vars documented in `deploy/.env.example`: `CONTROL_API_TOKEN`,
  `CLOUDFLARE_TUNNEL_TOKEN`.

### 3. Stream Deck plugin — `streamdeck-plugin/` (new, top-level)

Five key actions, all sharing one API client and Stream Deck **global
settings** (`apiUrl`, `apiToken`, `discordUserId` — entered once in any
action's Property Inspector, shared by all):

| Action | UUID suffix | Behavior |
|---|---|---|
| Play/Pause | `.play-pause` | POST play-pause; key icon reflects paused/playing from now-playing polling. |
| Skip | `.skip` | POST skip; `showOk` flash on success. |
| Stop | `.stop` | POST stop; `showOk` flash on success. |
| Volume Up | `.volume-up` | POST volume `{delta:+5}`. |
| Volume Down | `.volume-down` | POST volume `{delta:-5}`. |
| Now Playing | `.now-playing` | Polls `GET /control/now-playing`; renders title (marquee-truncated) + paused indicator via `setTitle`/`setImage`. |

- **Polling:** one shared poller (5 s interval) runs while any Play/Pause
  or Now Playing key is visible (`onWillAppear`/`onWillDisappear`
  refcounting). Backs off to 30 s after consecutive failures; recovers on
  first success.
- **Idle state:** `active: false` → Now Playing shows "No session"; action
  keys stay enabled but a 409 response triggers `showAlert` (brief flash,
  no dialog).
- Packaging: `streamdeck pack` output committed to a GitHub release or
  kept local; install by double-click.

## Data flow

```
key press ── plugin ── HTTPS(Bearer) ──► control.<domain>
  ──► cloudflared (Cloudflare edge → tunnel) ──► bot:8080 /control/*
  ──► resolve guild via Gateway voice state ──► PlayerService.<action>
  ──► Lavalink ──► Discord voice
(now-playing poll follows the same path in reverse)
```

## Error handling

- **No active session / user not in voice:** 409 on actions (key flashes
  alert), `{"active": false}` on now-playing (idle icon). Never a hard error.
- **Network / tunnel down:** plugin catches fetch failures, shows
  disconnected state on Now Playing, backs off polling 5 s → 30 s.
- **401 (bad token):** Now Playing shows "Auth error"; actions flash alert.
  Surfaced once per state change, not logged per-press.
- **Missing settings:** keys show "Setup needed" until `apiUrl`,
  `apiToken`, and `discordUserId` are all present.
- **Volume bounds:** bot clamps; out-of-range delta is not an error.

## Security

- Bearer token is the only secret; lives in bot env + Stream Deck global
  settings. Rotation = change env var, redeploy, update Property Inspector.
- VM exposes no new inbound ports; Cloudflare terminates TLS and the
  tunnel is outbound-only from the VM.
- Control surface is playback-only (no queue mutation, no session
  creation), limiting blast radius if the token leaks.
- Optional hardening (not v1): Cloudflare Access service-token policy in
  front of the hostname.

## Testing

- **Bot (pytest, in `make test` scope):** auth (missing/wrong/right token,
  unset token → routes absent), session resolution (user not in voice, in
  voice without session, in voice with session, multi-guild first-match),
  handler → `PlayerService` call wiring, volume clamping, 409 paths.
  Discord objects mocked, consistent with existing service tests.
- **Plugin:** unit tests (vitest) for the API client and poller backoff
  logic; action classes kept thin enough that manual testing covers them.
- **Manual end-to-end:** deploy to VM, `curl` each route through the
  tunnel with/without token, then full Stream Deck walkthrough: play/pause
  toggle, skip, stop, volume, now-playing updates, idle behavior after
  leaving voice, behavior with bot stopped.

## Out of scope (v1)

- Starting/joining a session from the Stream Deck (`j!session` equivalent).
- Queue browsing/mutation, seek, loop, shuffle.
- Stream Deck+ dials/touchstrip, album-art thumbnails on keys.
- Marketplace submission, multi-user distribution polish.
- Legacy v1 bot (`bot/`) — v2 only.
