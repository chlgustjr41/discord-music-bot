# Stream Deck OAuth2 + Summon Key Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static control-API token with Discord OAuth2 per-user tokens (zero-typing sign-in from the Stream Deck Property Inspector) and add a Summon key that joins/leaves a configured voice channel.

**Architecture:** Bot gains `/control/auth/*` (OAuth start/callback/poll, membership-gated token minting, SHA-256-at-rest storage in Firestore) and reworked `/control/*` auth (per-user bearer → server-derived identity, sliding-window rate limit), plus `/control/channels` and `/control/summon`. Plugin gains an auth module (open browser + poll), a sign-in PI, and a Summon action with per-key guild/channel dropdowns.

**Tech Stack:** unchanged from v1 (aiohttp/pytest; TypeScript/@elgato/streamdeck 1.x/vitest).

**Spec:** `docs/superpowers/specs/2026-08-07-streamdeck-oauth-summon-design.md` — read it first; it is the contract. Where this plan says "per spec", the spec section governs.

**House rules for every task:** TDD (tests first, red, implement, green); run `cd services/bot && py -m pytest -q` + `uvx ruff@0.15.20 check services/bot` for bot tasks, `cd streamdeck-plugin && npm test && npm run build` for plugin tasks; commit per task with the given message. Mirror existing file/module conventions — when in doubt, read the neighboring module named in the task.

---

## File Structure

**Bot (`services/bot/`):**
| File | Change | Responsibility |
|---|---|---|
| `src/jacky/config.py` | modify | `discord_client_id`/`discord_client_secret` replace `control_api_token` |
| `src/jacky/state/repository.py` | modify | `controlTokens` collection CRUD (sync-inner/async-`_run` pattern) |
| `src/jacky/api/tokens.py` | create | `TokenStore`: mint/resolve/revoke/touch, hash-at-rest, TTL cache |
| `src/jacky/api/ratelimit.py` | create | `SlidingWindow` per-key limiter |
| `src/jacky/api/oauth.py` | create | Discord code-exchange + identity fetch (injectable) |
| `src/jacky/api/auth_routes.py` | create | start/callback/poll + pending-state lifecycle + HTML pages |
| `src/jacky/api/control.py` | rework | bearer→TokenStore auth, contract change, channels, summon |
| `src/jacky/commands/status.py` | modify | `j!unlink` |
| `src/jacky/core/bot.py` | modify | gate + construction wiring |
| `tests/conftest.py` | modify | FakeRepo token methods, FakeGuild channels/fetch_member |
| `tests/test_control_api.py` | rework | new auth model; keep behavioral coverage |
| `tests/test_auth_routes.py`, `tests/test_tokens.py` | create | per-module suites |

**Plugin (`streamdeck-plugin/`):** `src/auth.ts` (new), `src/settings.ts`, `src/api-client.ts`, `src/runtime.ts` (rework), `src/actions/summon.ts` (new), `src/plugin.ts`, `ui/settings.html` (+ inline script), manifest (7th action, v0.2.0.0), `imgs/summon*.svg`; tests `tests/auth.test.ts`, `tests/api-client.test.ts`.

**Deploy/docs:** `deploy/docker-compose.yml`, `deploy/.env.example`, `docs/streamdeck-control.md`.

---

## Part 1 — Bot

### Task 1: Config + repository token CRUD + fakes

**Files:** `src/jacky/config.py`, `src/jacky/state/repository.py`, `tests/conftest.py`, `tests/test_config.py`

- [ ] Config: replace `control_api_token: str` with `discord_client_id: str` and `discord_client_secret: str` (both `os.environ.get(..., "")`, env names `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET`). Update `test_control_api_token_defaults_empty` → `test_discord_oauth_settings_default_empty` (same shape, both vars).
- [ ] Repository — append a `# ── control tokens ──` section following the file's sync-inner/async-outer `_run` pattern exactly:

```python
    def _save_control_token(self, token_hash: str, data: dict) -> None:
        self.db.collection("controlTokens").document(token_hash).set(data)

    async def save_control_token(self, token_hash: str, data: dict) -> None:
        await self._run(self._save_control_token, token_hash, data)

    def _get_control_token(self, token_hash: str) -> dict | None:
        doc = self.db.collection("controlTokens").document(token_hash).get()
        return doc.to_dict() if doc.exists else None

    async def get_control_token(self, token_hash: str) -> dict | None:
        return await self._run(self._get_control_token, token_hash)

    def _delete_control_tokens_for_user(self, user_id: str) -> int:
        docs = list(
            self.db.collection("controlTokens").where("userId", "==", user_id).stream()
        )
        for doc in docs:
            doc.reference.delete()
        return len(docs)

    async def delete_control_tokens_for_user(self, user_id: str) -> int:
        return await self._run(self._delete_control_tokens_for_user, user_id)

    def _touch_control_token(self, token_hash: str, iso_now: str) -> None:
        self.db.collection("controlTokens").document(token_hash).set(
            {"lastUsed": iso_now}, merge=True
        )

    async def touch_control_token(self, token_hash: str, iso_now: str) -> None:
        await self._run(self._touch_control_token, token_hash, iso_now)
```

- [ ] FakeRepo (`tests/conftest.py`): add `self.control_tokens: dict[str, dict] = {}` and async equivalents of the four methods operating on that dict (delete returns count of removed entries whose `userId` matches).
- [ ] FakeGuild additions (needed by Tasks 4–5): field `voice_channels: list = field(default_factory=list)` (populate from `add_voice_channel`, which should also append); async `fetch_member(self, user_id)` that returns `members_by_id[user_id]` or raises a `FakeNotFound(Exception)` defined in conftest (mirrors `discord.NotFound` semantics — the real code will catch `discord.NotFound`; tests monkeypatch or the code catches a tuple, see Task 5 note).
- [ ] Verify suite green; commit: `feat(control): oauth settings + controlTokens repository CRUD`

### Task 2: TokenStore + SlidingWindow

**Files:** `src/jacky/api/tokens.py`, `src/jacky/api/ratelimit.py`, `tests/test_tokens.py`

- [ ] Tests first (`tests/test_tokens.py`), covering: mint returns 64-hex token and persists ONLY the sha256 (assert raw token not in FakeRepo keys; assert `hashlib.sha256(token.encode()).hexdigest()` IS a key, with `{userId, userName, createdAt, lastUsed}`); resolve(valid)→userId; resolve(unknown)→None; resolve caches (second call hits no repo — count FakeRepo get calls via a wrapper); revoke_user deletes from repo AND cache (resolve returns None after revoke even though cached before); touch throttling (two touches within 60 s → one repo write; monkeypatch `time.monotonic`); SlidingWindow: allows `limit` hits then blocks, window expiry re-allows (monkeypatch monotonic).
- [ ] Implement `tokens.py`:

```python
"""Per-user control-API tokens. Raw token only ever exists client-side;
Firestore stores sha256(token) as the document id (spec §Decisions)."""

import datetime
import hashlib
import secrets
import time
from typing import Any

CACHE_TTL_S = 300.0
TOUCH_MIN_INTERVAL_S = 60.0


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class TokenStore:
    def __init__(self, repo: Any) -> None:
        self.repo = repo
        self._cache: dict[str, tuple[str, float]] = {}  # hash -> (userId, cached_at)
        self._last_touch: dict[str, float] = {}

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    async def mint(self, user_id: str, user_name: str) -> str:
        token = secrets.token_hex(32)
        h = self._hash(token)
        now = _now_iso()
        await self.repo.save_control_token(h, {
            "userId": user_id, "userName": user_name,
            "createdAt": now, "lastUsed": now,
        })
        self._cache[h] = (user_id, time.monotonic())
        return token

    async def resolve(self, token: str) -> str | None:
        h = self._hash(token)
        hit = self._cache.get(h)
        if hit and time.monotonic() - hit[1] < CACHE_TTL_S:
            await self._maybe_touch(h)
            return hit[0]
        data = await self.repo.get_control_token(h)
        if data is None:
            self._cache.pop(h, None)
            return None
        self._cache[h] = (data["userId"], time.monotonic())
        await self._maybe_touch(h)
        return data["userId"]

    async def _maybe_touch(self, h: str) -> None:
        now = time.monotonic()
        if now - self._last_touch.get(h, 0.0) >= TOUCH_MIN_INTERVAL_S:
            self._last_touch[h] = now
            await self.repo.touch_control_token(h, _now_iso())

    async def revoke_user(self, user_id: str) -> int:
        count = await self.repo.delete_control_tokens_for_user(user_id)
        self._cache = {h: v for h, v in self._cache.items() if v[0] != user_id}
        return count
```

- [ ] Implement `ratelimit.py`:

```python
"""In-memory sliding-window limiter, keyed by opaque string (token hash)."""

import time


class SlidingWindow:
    def __init__(self, limit: int = 30, window_s: float = 10.0) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = [t for t in self._hits.get(key, []) if t > now - self.window_s]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True
```

- [ ] Green + ruff; commit: `feat(control): TokenStore (sha256 at rest, TTL cache) + sliding-window rate limit`

### Task 3: OAuth client + auth routes

**Files:** `src/jacky/api/oauth.py`, `src/jacky/api/auth_routes.py`, `tests/test_auth_routes.py`

- [ ] `oauth.py` — injectable, thin:

```python
"""Discord OAuth2 (authorization-code, identify scope). Docs:
https://discord.com/developers/docs/topics/oauth2"""

from typing import Any

AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = "https://discord.com/api/oauth2/token"
ME_URL = "https://discord.com/api/users/@me"


class DiscordOAuth:
    def __init__(self, http: Any, client_id: str, client_secret: str,
                 redirect_uri: str) -> None:
        self.http = http  # aiohttp.ClientSession
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    def authorize_url(self, state: str) -> str:
        from urllib.parse import urlencode
        return AUTHORIZE_URL + "?" + urlencode({
            "client_id": self.client_id, "response_type": "code",
            "redirect_uri": self.redirect_uri, "scope": "identify",
            "state": state, "prompt": "none",
        })

    async def exchange_code(self, code: str) -> str:
        async with self.http.post(TOKEN_URL, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": self.redirect_uri,
        }) as resp:
            if resp.status != 200:
                raise OAuthError(f"token exchange failed: {resp.status}")
            return (await resp.json())["access_token"]

    async def fetch_identity(self, access_token: str) -> dict:
        async with self.http.get(
            ME_URL, headers={"Authorization": f"Bearer {access_token}"}
        ) as resp:
            if resp.status != 200:
                raise OAuthError(f"identity fetch failed: {resp.status}")
            data = await resp.json()
            return {"id": str(data["id"]), "username": data.get("username", "")}


class OAuthError(Exception):
    pass
```

- [ ] `auth_routes.py` — `register_auth_routes(app, *, bot, repo, oauth, token_store, member_gate)` where `member_gate(user_id) -> bool` is passed in (built in Task 6 wiring; tests inject a stub). Behavior per spec: `POST /control/auth/start` (per-IP `SlidingWindow(10, 60)`; mints `secrets.token_urlsafe(32)` state, stores `{createdAt}`, returns `{"state": ..., "authorizeUrl": oauth.authorize_url(state)}`); `GET /control/auth/callback?code&state` (unknown/expired state → 410 HTML; `OAuthError` → 502 HTML; member_gate False → 403 HTML "not in any server Jacky serves"; success → mint via token_store, stash `{token, userId, userName}` on the state entry, 200 HTML "Signed in — you can close this tab"); `GET /control/auth/poll?state=` (unknown/expired → 410 JSON; pending → 202 `{"status":"pending"}`; ready → 200 `{token, discordUserId, discordUserName}` and DELETE the entry — one-time claim). States expire 600 s after creation; sweep lazily on every start/poll. HTML pages: small inline-styled strings in the module, no assets.
- [ ] Tests (`tests/test_auth_routes.py`) with a `FakeOAuth` (records calls; programmable identity/failure) and stub `member_gate`; aiohttp TestClient like the existing control tests. Cases: start returns state+url containing state & client_id; state expiry (monkeypatch the module's clock or entry createdAt) → callback 410 and poll 410; callback happy path → poll 200 exactly once then 410; poll before callback → 202; callback with exchange failure → 502; non-member → 403 and poll stays pending-forever (entry marked failed → poll returns 403 JSON `{"error":"not-a-member"}` — pick this, test it); auth/start rate limit → 429 on the 11th call from one IP.
- [ ] Green + ruff; commit: `feat(control): discord oauth client + auth routes (start/callback/poll)`

### Task 4: Control routes rework — bearer identity + channels

**Files:** `src/jacky/api/control.py`, `tests/test_control_api.py`

- [ ] Rework `register_control_routes(app, *, bot, service, token_store, limiter)`. `guarded(handler)` now: extract bearer → `await token_store.resolve(token)` → None → 401; `limiter.allow(sha256(token))` False → 429 `{"error":"rate-limited"}`; else call `handler(request, user_id)`. All five v1 handlers drop `discordUserId` inputs (`resolve_guild(user_id)` unchanged internally; `action_target` loses its user-id parsing). `body_of` retained for volume/summon.
- [ ] New `GET /control/channels` → for each guild in `bot.guilds` where `await service.repo.is_activated(str(guild.id))` and `guild.get_member(user_id)`: `{"guildId": str(guild.id), "guildName": guild.name, "channels": [{"id": str(c.id), "name": c.name} for c in guild.voice_channels]}`. (Cache-only membership here is acceptable: the PI refreshes after summon use; note this in a comment.)
- [ ] Rework `tests/test_control_api.py`: fixture builds TokenStore over the FakeRepo, mints a token for USER_ID in setup, AUTH header uses that token. Update every existing test to the new contract (no discordUserId params/fields). Keep ALL v1 behavioral cases (auth sweep, resolution matrix, volume-0, toggle, teardown, clamp) plus new: unknown token 401; revoked-mid-session token 401 (mint, revoke_user, then call); 429 after 30 calls in-window (construct limiter with small numbers via injection); channels happy path / not-activated guild excluded / non-member guild excluded.
- [ ] Green + ruff; commit: `feat(control)!: per-user bearer auth, discordUserId removed from contract, channels endpoint`

### Task 5: Summon endpoint

**Files:** `src/jacky/api/control.py`, `tests/test_control_api.py`

- [ ] `POST /control/summon` body `{"guildId", "channelId"}` (strings; 400 if missing/non-numeric). Per spec §2: membership check `guild.get_member(user_id)` then `await guild.fetch_member(user_id)` fallback catching `Exception` from the fake/`discord.NotFound` (catch narrowly: `except (discord.NotFound, discord.HTTPException)` in real code — import discord at module top is fine, it's already a bot dependency; conftest's FakeNotFound tests the negative path by monkeypatching the summon module's caught types OR simpler: code catches `Exception` with a comment — NO: catch `(discord.NotFound, discord.HTTPException)` and have FakeGuild.fetch_member raise `discord.NotFound(response=SimpleNamespace(status=404, reason=""), message="")`... if constructing discord.NotFound proves awkward in tests, define in control.py `_MEMBER_LOOKUP_ERRORS: tuple = (discord.NotFound, discord.HTTPException)` and monkeypatch it in tests — choose this, it's clean). Non-member → 403 `{"error":"not-a-member"}`; not activated → 403 `{"error":"not-activated"}`; bot connected to same channel → `await service.teardown_session(guild.id, requeue_current=True)` → `{"action":"left"}`; connected elsewhere in guild → 409 `{"error":"active-elsewhere"}`; not connected → channel lookup via `guild.get_channel(int(channelId))`, must exist and have `connect` (else 400 `{"error":"bad-channel"}`), then `await channel.connect(cls=LavalinkVoiceClient)` (function-level import, matching player.py), `code = await service.begin_session(guild, channel)` → `{"action":"joined","sessionCode":code}`; connect/begin failure → 502 `{"error":"join-failed"}` (log exception).
- [ ] Tests: join happy path (asserts voice_client set, session code returned, state initialized); leave happy path (queue preserved — seed currentTrack, assert requeued); active-elsewhere 409; non-member 403 (both cache-miss+fetch-miss); not-activated 403 (FakeRepo.activated toggle exists); bad channel 400; auth still enforced (covered by sweep — confirm sweep picks up the new route automatically).
- [ ] Green + ruff; commit: `feat(control): summon toggle endpoint (join/leave configured channel)`

### Task 6: j!unlink + wiring

**Files:** `src/jacky/commands/status.py` (read it; if the cog layout fits better in another cog, say so and place accordingly), `src/jacky/core/bot.py`, `tests/test_control_api.py` (wiring test only)

- [ ] `j!unlink`: `count = await self.bot.token_store.revoke_user(str(ctx.author.id))`; reply success embed `f"Unlinked {count} Stream Deck sign-in(s)."` (0 is fine — still success wording "No active sign-ins."). Guard: if the bot has no token_store (feature disabled) reply error embed "Stream Deck control is not enabled."
- [ ] `core/bot.py` setup_hook: replace the v1 `control_api_token` block with: if `settings.discord_client_id and settings.discord_client_secret`: build `DiscordOAuth(self.http_session, ..., redirect_uri=f"{PUBLIC_CONTROL_URL}/control/auth/callback")` — define `public_control_url: str` in Settings (env `PUBLIC_CONTROL_URL`, default `https://control.jacky-music-bot.com`); `self.token_store = TokenStore(self.repo)`; `member_gate` closure per spec (activated guilds, cache→REST); `register_auth_routes(...)`; `register_control_routes(app, bot=self, service=self.service, token_store=self.token_store, limiter=SlidingWindow())`. Else `self.token_store = None`.
- [ ] Green + ruff (full suite); commit: `feat(control): j!unlink + oauth wiring, static token gate removed`

## Part 2 — Deploy & docs

### Task 7: Compose/env/runbook

**Files:** `deploy/docker-compose.yml`, `deploy/.env.example`, `docs/streamdeck-control.md`

- [ ] compose bot env: remove `CONTROL_API_TOKEN`, add `DISCORD_CLIENT_ID: ${DISCORD_CLIENT_ID:-}` and `DISCORD_CLIENT_SECRET: ${DISCORD_CLIENT_SECRET:-}` (and `PUBLIC_CONTROL_URL: ${PUBLIC_CONTROL_URL:-}` passthrough, optional var).
- [ ] `.env.example`: replace the CONTROL_API_TOKEN block with the two Discord vars — comment with exact portal path (Applications → your app → OAuth2: copy Client ID; Reset Secret for the secret; add redirect `https://control.<your-domain>/control/auth/callback`). Keep CLOUDFLARE_TUNNEL_TOKEN/COMPOSE_PROFILES as-is.
- [ ] Runbook rework: server setup step 1 becomes the portal step; add "Onboarding a friend" section (install file → drag key → Sign in with Discord → member requirement); token rotation section replaced by `j!unlink` + re-sign-in; keep tunnel/verify sections (verify curl now expects 401 `{"error":"unauthorized"}` without bearer — unchanged — and notes the with-token curl now requires a minted user token, easiest via the plugin, or grep Firestore).
- [ ] Compose parse checks (both env-file variants, per Task 6 of the v1 plan); commit: `chore(deploy): discord oauth env contract + runbook v2`

## Part 3 — Plugin

### Task 8: api-client + auth module

**Files:** `src/settings.ts`, `src/api-client.ts`, `src/auth.ts` (new), `tests/api-client.test.ts`, `tests/auth.test.ts` (new)

- [ ] `settings.ts`: `GlobalSettings = { apiUrl?; authToken?; discordUserId?; discordUserName? }`; `export const DEFAULT_API_URL = "https://control.jacky-music-bot.com";` `export function effectiveApiUrl(s: GlobalSettings): string` (apiUrl trimmed or default); `settingsReady` = `Boolean(effectiveApiUrl(s) && s.authToken)`.
- [ ] `api-client.ts`: constructor takes `{ apiUrl: string; authToken: string }`; drop discordUserId from every method/body/query; add types + methods:

```ts
export type ChannelList = {
  guildId: string; guildName: string;
  channels: { id: string; name: string }[];
}[];
export type SummonResult = { action: "joined" | "left"; sessionCode?: string };
```

`channels(): Promise<ChannelList>` (GET `/control/channels`), `summon(guildId: string, channelId: string): Promise<SummonResult>` (POST, body `{guildId, channelId}`, parses JSON on 200, throws ControlApiError otherwise). Keep timeouts/name/etc.
- [ ] `auth.ts`:

```ts
import { ControlApiError } from "./api-client";

export type SignInResult = { token: string; discordUserId: string; discordUserName: string };

const POLL_MS = 2000;
const TIMEOUT_MS = 5 * 60 * 1000;

/** Start OAuth: returns the URL to open plus a promise resolving when the
 *  user completes sign-in in the browser (or rejecting on 410/timeout). */
export async function signIn(
  apiUrl: string,
  openUrl: (url: string) => void,
  fetchFn: typeof fetch = fetch,
): Promise<SignInResult> {
  const base = apiUrl.replace(/\/+$/, "");
  const startRes = await fetchFn(`${base}/control/auth/start`, { method: "POST" });
  if (!startRes.ok) throw new ControlApiError(startRes.status);
  const { state, authorizeUrl } = (await startRes.json()) as {
    state: string; authorizeUrl: string;
  };
  openUrl(authorizeUrl);
  const deadline = Date.now() + TIMEOUT_MS;
  for (;;) {
    await new Promise((r) => setTimeout(r, POLL_MS));
    const res = await fetchFn(
      `${base}/control/auth/poll?state=${encodeURIComponent(state)}`,
    );
    if (res.status === 202) {
      if (Date.now() > deadline) throw new ControlApiError(408);
      continue;
    }
    if (!res.ok) throw new ControlApiError(res.status);
    const body = (await res.json()) as {
      token: string; discordUserId: string; discordUserName: string;
    };
    return { token: body.token, discordUserId: body.discordUserId,
             discordUserName: body.discordUserName };
  }
}
```

(`Date.now` + fake timers work together under vitest's `vi.useFakeTimers()` when advancing with `advanceTimersByTimeAsync`, which also advances the mocked clock.)
- [ ] Tests: api-client — updated wire assertions (no discordUserId anywhere; channels URL/headers; summon body/response parse; summon non-2xx throws with status). auth — happy path (start → openUrl called with authorizeUrl → two 202s → 200 resolves with token); 410 rejects with status 410; timeout (>5 min of 202s) rejects with 408; start failure rejects.
- [ ] Green + build; commit: `feat(deck): oauth sign-in module + v2 api client (identity from token)`

### Task 9: PI, runtime, summon action, manifest, registration

**Files:** `src/runtime.ts`, `src/plugin.ts`, `src/actions/summon.ts` (new), `ui/settings.html`, manifest, `imgs/summon.svg` + `imgs/summoned.svg`

- [ ] `runtime.ts`: build client from `effectiveApiUrl` + `authToken`; export `signInFlow(): Promise<void>` that calls `auth.signIn(effectiveApiUrl(current), (u) => streamDeck.system.openUrl(u))`, then merges `{authToken, discordUserId, discordUserName}` into global settings via `streamDeck.settings.setGlobalSettings` (apply() then runs via the settings event; also call `poller.kick()` directly for immediacy).
- [ ] Plugin-side PI messaging: in each action (or a small shared helper mixed into the base), handle `onSendToPlugin`: payload `{event:"sign-in"}` → run signInFlow, then `sendToPropertyInspector({event:"auth-status", ok, userName?, error?})`; payload `{event:"get-channels"}` → `client.channels()` → `sendToPropertyInspector({event:"channels", data})` (or `{event:"channels-error"}`). Implement the handler once in a shared module `src/pi-bridge.ts` and call it from each action's `onSendToPlugin` override (SingletonAction receives the event per-action).
- [ ] `ui/settings.html` rework: keep sdpi-components; global section = current-status text element + "Sign in with Discord" `<sdpi-button>`; a `<div id="summon-settings">` (hidden unless the PI's action UUID ends `.summon` — check `actionInfo` from `connectElgatoStreamDeckSocket`/sdpi's `SDPIComponents.streamDeckClient` info) containing two `<sdpi-select>` (guild, channel) bound to per-action settings `guildId`/`channelId`; inline `<script>`: on load request channels + status via `sendToPlugin`, populate selects on the reply, wire button click → `sendToPlugin({event:"sign-in"})`, update status line on `auth-status`. Use `SDPIComponents.streamDeckClient.send`/`onDidReceivePlugin...` APIs from sdpi-components v4 (consult vendored `ui/sdpi-components.js` d.ts-less API: `SDPIComponents.streamDeckClient.sendToPlugin(payload)` and `.didReceivePluginMessage.subscribe(cb)` — verify against the vendored source; adapt if names differ, report adaptations).
- [ ] `src/actions/summon.ts`: UUID `com.jacobchoi.jacky-control.summon`, per-action settings `{guildId?, channelId?}` read from `ev.action.getSettings()`; onKeyDown: missing settings or no client → showAlert; else `client.summon(guildId, channelId)` → setState(action === "joined" ? 1 : 0) + showOk; catch → showAlert. Include the shared PI-bridge handling.
- [ ] Manifest: add the Summon action (Name "Summon", Tooltip "Join or leave the configured voice channel", States [imgs/summon, imgs/summoned], PropertyInspectorPath ui/settings.html, Controllers [Keypad]); bump Version `0.2.0.0`. Icons: `summon.svg` = coral door/arrow-in motif on the house dark square; `summoned.svg` = same with the arrow reversed/gold `#f5b942` accent (author simple shapes in the style of the existing icons).
- [ ] `plugin.ts`: register Summon too.
- [ ] Build + `npm test` + validate (0 errors); commit: `feat(deck): sign-in PI, summon key, v0.2.0.0`

### Task 10: Deploy + live E2E + pack (manual/controller)

- [ ] Operator: Discord portal redirect + client id/secret → VM `deploy/.env` (remove CONTROL_API_TOKEN line), `make up`.
- [ ] Curls: `/control/now-playing` no-auth → 401; `/control/auth/start` POST → JSON with authorizeUrl.
- [ ] Own-account sign-in through the deck; walkthrough all keys incl. Summon join/leave/elsewhere; `j!unlink` → keys 401 → re-sign-in; friend-flow if a second account is available.
- [ ] `npx @elgato/cli pack ... --force`; deliver the file; update memory/STATUS notes.
