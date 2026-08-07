# Stream Deck OAuth2 Auth + Summon Key — Design

**Date:** 2026-08-07
**Status:** Approved
**Scope:** v2 of the Stream Deck control feature (spec v1: `2026-08-06-streamdeck-session-control-design.md`, live in prod). Replaces the static `CONTROL_API_TOKEN` with Discord OAuth2 per-user tokens, and adds a Summon key (bot joins/leaves a channel configured per-key). Touches `services/bot/`, `deploy/`, `streamdeck-plugin/`, docs. Frontend, guardian, Lavalink untouched.

## Problem

The v1 static token is a single shared secret: unauthenticated `discordUserId`,
manual URL/token/ID entry, all-or-nothing rotation — unshareable beyond the
owner. Goal: a friend installs the sideloaded `.streamDeckPlugin`, clicks
**Sign in with Discord**, and is done — no typed settings. Additionally: a
key that summons the bot into a specific voice channel (and dismisses it),
channel chosen in the key's settings.

## Decisions

| Question | Decision |
|---|---|
| Auth | Discord OAuth2 authorization-code flow, `identify` scope only. Backend holds the client secret; plugin never sees Discord credentials. |
| Sign-in transport | PI button → `sendToPlugin` → plugin (Node) calls `POST /control/auth/start`, opens system browser to the authorize URL, polls `GET /control/auth/poll` (2 s, ≤5 min), writes global settings itself. No CORS, no localhost listener. |
| Who may get a token | Membership gate: after code exchange, the bot must find the user in ≥1 **activated** guild (`guild.get_member` cache first, `guild.fetch_member` REST fallback — works without the privileged members intent). Otherwise 403, friendly HTML. |
| Token format/storage | `secrets.token_hex(32)`; only its SHA-256 stored: Firestore collection `controlTokens/{sha256}` → `{userId, userName, createdAt, lastUsed}`. In-memory cache (hash→userId, TTL 300 s). Multiple tokens per user = multiple devices. |
| Legacy token | `CONTROL_API_TOKEN` removed from code, compose, env docs. OAuth is the only path. Deployed v0.1 plugins get 401 until re-signed-in (acceptable: single known user). |
| Contract change | `discordUserId` removed from every route; identity derives from the bearer token server-side. |
| Revocation | `j!unlink` prefix command deletes all of the caller's tokens (and cache entries). Re-sign-in any time. |
| Rate limit | Per-token in-memory sliding window: 30 requests / 10 s → 429. |
| Summon semantics | One toggle key, per-key settings `{guildId, channelId}` picked from a dropdown. Press: bot in that exact channel → leave (queue preserved, `requeue_current=True`); bot elsewhere in that guild → 409 `active-elsewhere` (alert flash); else join + begin session (activation required, reusing the `handle_summon` path semantics). |
| Channel discovery | `GET /control/channels` → activated guilds where the caller is a member: `[{guildId, guildName, channels: [{id, name}]}]` from the Gateway channel cache. PI populates its dropdown through the plugin (`sendToPlugin`/`sendToPropertyInspector`). |
| Auth routes exposure | Mounted under `/control/auth/*` so the existing Cloudflare path rule (`control`) forwards them — zero tunnel changes. |
| New env vars | `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` (from the existing Discord application). Control API registers only when both present (replaces the token gate). |

## Prerequisite (operator)

Discord Developer Portal → the bot's application → OAuth2: add redirect URI
`https://control.jacky-music-bot.com/control/auth/callback`; copy Client ID
and Client Secret into `deploy/.env`.

## Components

### 1. Bot — `services/bot/src/jacky/api/` (rework)

- **`oauth.py` (new):** thin Discord OAuth client over the bot's existing
  aiohttp session: `exchange_code(code) -> access_token`,
  `fetch_identity(access_token) -> {id, username}`. Injectable for tests.
- **`tokens.py` (new):** `TokenStore` — mint (hex + sha256), Firestore
  persistence via `ServerRepository` extensions, in-memory cache with TTL,
  `resolve(bearer) -> userId | None`, `revoke_user(userId) -> count`,
  `touch(hash)` updating `lastUsed` (throttled, ≥60 s apart).
- **`auth_routes.py` (new):** start/callback/poll. Pending-state dict
  `{state: {createdAt, token?, userId?, userName?}}`, TTL 600 s, swept
  lazily; `poll` claims exactly once then deletes. Callback returns small
  inline-styled HTML (success / not-a-member / error).
- **`control.py` (rework):** `register_control_routes(app, *, bot, service,
  token_store, rate_limiter)`; `guarded()` resolves the bearer via
  TokenStore → 401 unknown, applies the rate limit → 429, injects `user_id`
  into handlers. Routes now: now-playing, play-pause, skip, stop, volume
  (bodies lose `discordUserId`), plus `GET /control/channels` and
  `POST /control/summon`.
- **`ratelimit.py` (new):** `SlidingWindow(limit=30, window_s=10)` keyed by
  token hash, in-memory.
- **`commands/status.py` (or playback.py, match existing cog layout):**
  `j!unlink` — revokes caller's tokens, replies with count.
- **`core/bot.py`:** gate on `settings.discord_client_id and
  settings.discord_client_secret`; construct TokenStore/oauth client;
  remove `control_api_token` from `config.py`.
- **`state/repository.py` + FakeRepo:** `save_control_token(hash, data)`,
  `get_control_token(hash)`, `delete_control_tokens_for_user(userId)`,
  `touch_control_token(hash)`.

### 2. Summon endpoint

`POST /control/summon` body `{guildId, channelId}` (strings):
1. Caller must be a member of that guild (cache→REST fallback) → else 403.
2. Guild must be activated → else 403.
3. Bot voice client in that guild: connected to `channelId` →
   `teardown_session(guild_id, requeue_current=True)` → `{"action": "left"}`;
   connected elsewhere → 409 `{"error": "active-elsewhere"}`.
4. Not connected: validate channel exists & connectable, connect with
   `LavalinkVoiceClient`, `begin_session`, → `{"action": "joined",
   "sessionCode": code}`.

### 3. Plugin — `streamdeck-plugin/`

- **`src/auth.ts` (new):** `signIn(apiUrl): Promise<{token, userId, userName}>`
  — start → `streamDeck.system.openUrl(authorizeUrl)` → poll loop (2 s,
  5 min timeout, abortable).
- **`src/settings.ts`:** global settings `{apiUrl?, authToken?,
  discordUserId?, discordUserName?}`; `settingsReady` = apiUrl+authToken.
  `DEFAULT_API_URL = "https://control.jacky-music-bot.com"` applied when
  unset.
- **`src/api-client.ts`:** drop `discordUserId` everywhere; add
  `channels()`, `summon(guildId, channelId)`; 429 surfaces as
  ControlApiError(429) (poller treats as offline-ish; actions flash alert).
- **PI (`ui/settings.html` + small `ui/pi.js`):** Sign-in button + status
  line (global section, all actions); Summon key additionally shows
  guild/channel `<sdpi-select>` populated via plugin round-trip; selections
  stored as per-action settings `{guildId, channelId}`.
- **`src/actions/summon.ts` (new):** UUID `.summon`, 2 states (0 out /
  1 in). onKeyDown → `summon()`; key state set from the `{"action"}`
  response only — no per-channel polling in v1 (documented limitation:
  the icon can go stale if the bot leaves by other means).
- **Plugin plumbing:** `onSendToPlugin` handlers for `"sign-in"` and
  `"get-channels"`; replies via `sendToPropertyInspector`. `runtime.ts`
  builds the client from `{apiUrl (defaulted), authToken}`.
- **Manifest:** 7th action Summon (icon: door/arrow motif), version bump
  `0.2.0.0`.

### 4. Deploy & docs

- compose `bot` env: replace `CONTROL_API_TOKEN` with `DISCORD_CLIENT_ID`,
  `DISCORD_CLIENT_SECRET`; `.env.example` updated (portal instructions
  inline); runbook rewritten for the OAuth setup + friend-onboarding
  section; CLAUDE.md untouched.

## Error handling

- Auth: expired/unknown `state` → 410 on poll; Discord exchange failure →
  502 + error HTML; non-member → 403 + friendly HTML; poll pending → 202.
- Runtime: unknown/revoked token → 401 → PI status "Session expired — sign
  in again" (poller: `unauthorized` unchanged); 429 → actions alert flash,
  Now Playing unaffected (next poll succeeds).
- Summon: 403 non-member/not-activated (alert), 409 active-elsewhere
  (alert), join failure (voice connect raises) → 502 + alert.
- Plugin sign-in: browser never completed → poll timeout → PI status
  "Sign-in timed out"; retry re-mints state.

## Security

- Client secret only in bot env. `identify` scope only. `state` =
  `secrets.token_urlsafe(32)`, single-use, 10-min TTL.
- Raw bearer tokens never persisted server-side (SHA-256 only) and never
  logged; plugin stores its token in Stream Deck global settings (plaintext
  on the user's own disk — same trust level as any Stream Deck plugin).
- Membership gate at issuance; per-guild membership re-checked on summon.
- Rate limit per token; auth endpoints additionally rate-limited per-IP
  (10/60 s) to slow state-mint spam.
- Blast radius of a leaked token: that user's own sessions only; `j!unlink`.

## Testing

- **Bot (pytest):** oauth client faked; auth routes (state lifecycle,
  one-time claim, TTLs, membership gate pass/fail, HTML/status codes);
  TokenStore (hash storage, cache, revoke, touch throttle); control auth
  (valid/unknown/revoked bearer, 429 window); summon (member/non-member,
  not-activated, join/leave/active-elsewhere, queue preserved on leave);
  contract regression (discordUserId no longer accepted/required);
  `j!unlink`.
- **Plugin (vitest):** auth poll loop (fake timers: success, 410, timeout);
  api-client new methods + contract change.
- **Manual:** operator env setup; own-account sign-in; friend-account
  sign-in (non-member rejection with a throwaway account if available);
  summon join/leave/elsewhere; `j!unlink` → 401 → re-sign-in; rate-limit
  spot check.

## Out of scope (v2)

- Marketplace/SDK 2.x; Summon key live state polling; per-user permission
  tiers inside a guild; queue mutation from the deck; token expiry (tokens
  live until revoked).
