# Stream Deck Session Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Physical Stream Deck keys (play/pause, skip, stop, volume ±, now-playing) that control the user's *current* Jacky Music session — resolved from their live Discord voice-channel presence — from anywhere on the internet.

**Architecture:** A token-guarded `/control/*` REST surface mounts on the bot's existing aiohttp health app (port 8080, container-internal); a `cloudflared` named-tunnel sidecar (opt-in compose profile) publishes it as `https://control.<domain>` with no inbound VM ports. A new top-level `streamdeck-plugin/` (TypeScript, official `@elgato/streamdeck` SDK) is a thin client: six key actions sharing one API client and one refcounted poller, packaged with `streamdeck pack` for local install.

**Tech Stack:** Python 3.11 / aiohttp / pytest (bot); Node 20+ / TypeScript / rollup / vitest / `@elgato/streamdeck` v1 + `@elgato/cli` (plugin); Cloudflare Tunnel (deploy).

**Spec:** `docs/superpowers/specs/2026-08-06-streamdeck-session-control-design.md`

---

## File Structure

**Bot (`services/bot/`):**
| File | Change | Responsibility |
|---|---|---|
| `src/jacky/config.py` | modify | add `control_api_token` setting |
| `src/jacky/api/__init__.py` | create | package marker |
| `src/jacky/api/control.py` | create | `/control/*` routes: auth wrapper, session resolution, thin handlers over `PlayerService` |
| `src/jacky/core/health.py` | modify | `start_health_server` accepts an optional prebuilt app |
| `src/jacky/core/bot.py` | modify | build app, register control routes when token set |
| `tests/conftest.py` | modify | `FakeMember`/`FakeVoiceState`, `FakeGuild.get_member`, `FakeBot.is_ready` |
| `tests/test_control_api.py` | create | all control-API tests |

**Deploy (`deploy/`):** `docker-compose.yml` (bot env + `cloudflared` service under `control` profile), `.env.example` (new vars).

**Docs:** `docs/streamdeck-control.md` (tunnel + install runbook).

**Plugin (`streamdeck-plugin/`):**
| File | Responsibility |
|---|---|
| `package.json`, `tsconfig.json`, `rollup.config.mjs`, `.gitignore` | build scaffold |
| `com.jacobchoi.jacky-control.sdPlugin/manifest.json` | plugin + 6 action definitions |
| `com.jacobchoi.jacky-control.sdPlugin/ui/settings.html` | shared Property Inspector (global settings) |
| `com.jacobchoi.jacky-control.sdPlugin/imgs/*.svg` | icons |
| `src/settings.ts` | `GlobalSettings` type + readiness guard |
| `src/api-client.ts` | `JackyClient` HTTP wrapper |
| `src/format.ts` | `marquee()` title scroller |
| `src/poller.ts` | `SessionPoller` (refcount, 5 s → 30 s backoff) |
| `src/runtime.ts` | singleton client + poller, rebuilt on global-settings change |
| `src/actions/*.ts` | six thin action classes |
| `src/plugin.ts` | registration + connect |
| `tests/*.test.ts` | vitest for client/format/poller |

Run bot tests from `services/bot/`: `python -m pytest tests/test_control_api.py -v` (pytest-asyncio is in `auto` mode — plain `async def` tests work). Run plugin tests from `streamdeck-plugin/`: `npm test`.

---

## Part 1 — Bot Control API

### Task 1: `control_api_token` setting

**Files:**
- Modify: `services/bot/src/jacky/config.py`
- Test: `services/bot/tests/test_config.py`

- [ ] **Step 1: Write the failing test** — append to `services/bot/tests/test_config.py`:

```python
def test_control_api_token_defaults_empty(monkeypatch):
    for var, val in {
        "DISCORD_TOKEN": "t", "LAVALINK_HOST": "h", "LAVALINK_PORT": "2333",
        "LAVALINK_PASSWORD": "p", "FIREBASE_SERVICE_ACCOUNT_KEY": "/x.json",
    }.items():
        monkeypatch.setenv(var, val)
    monkeypatch.delenv("CONTROL_API_TOKEN", raising=False)
    from jacky.config import Settings
    assert Settings.from_env().control_api_token == ""

    monkeypatch.setenv("CONTROL_API_TOKEN", "secret123")
    assert Settings.from_env().control_api_token == "secret123"
```

(If the existing tests in that file build env differently — e.g. a shared helper — match their pattern instead; the assertions stay the same.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/bot && python -m pytest tests/test_config.py -v`
Expected: FAIL — `TypeError` (unexpected keyword) or `AttributeError: control_api_token`.

- [ ] **Step 3: Implement** — in `services/bot/src/jacky/config.py`, add a field after `empty_channel_timeout_seconds: int`:

```python
    control_api_token: str
```

and in `from_env()`, after the `empty_channel_timeout_seconds=...` entry:

```python
            control_api_token=os.environ.get("CONTROL_API_TOKEN", ""),
```

- [ ] **Step 4: Run to verify it passes** — same command, expected: PASS (all tests in file).

- [ ] **Step 5: Commit**

```bash
git add services/bot/src/jacky/config.py services/bot/tests/test_config.py
git commit -m "feat(control): CONTROL_API_TOKEN setting (empty = disabled)"
```

### Task 2: Conftest fakes for members/voice

**Files:**
- Modify: `services/bot/tests/conftest.py`

- [ ] **Step 1: Add fakes** — in `services/bot/tests/conftest.py`, add after the `FakeMe` dataclass:

```python
@dataclass
class FakeVoiceState:
    channel: object = None


@dataclass
class FakeMember:
    id: int
    voice: FakeVoiceState | None = None
```

Extend `FakeGuild` with a members store — add the field and method:

```python
    members_by_id: dict = field(default_factory=dict)

    def get_member(self, user_id):
        return self.members_by_id.get(user_id)
```

Give `FakeBot` a readiness probe (the health handler calls it once control tests exercise the combined app):

```python
    def is_ready(self):
        return True
```

- [ ] **Step 2: Verify nothing broke**

Run: `cd services/bot && python -m pytest -q`
Expected: all existing tests PASS.

- [ ] **Step 3: Commit**

```bash
git add services/bot/tests/conftest.py
git commit -m "test: FakeMember/voice-state + FakeGuild.get_member fakes"
```

### Task 3: Control routes — auth + session resolution + now-playing

**Files:**
- Create: `services/bot/src/jacky/api/__init__.py`, `services/bot/src/jacky/api/control.py`
- Test: `services/bot/tests/test_control_api.py`

- [ ] **Step 1: Write the failing tests** — create `services/bot/tests/test_control_api.py`:

```python
"""Control API: auth, voice-presence session resolution, playback handlers."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests.conftest import FakeMember, FakeVoiceState

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
USER_ID = 42


@pytest.fixture
async def client(service):
    from jacky.api.control import register_control_routes
    from jacky.core.health import build_app

    app = build_app(service.bot, service)
    register_control_routes(app, bot=service.bot, service=service, token=TOKEN)
    tc = TestClient(TestServer(app))
    await tc.start_server()
    yield tc
    await tc.close()


def put_user_in_voice(service, guild_id, user_id=USER_ID, channel_id=99):
    """Place a fake human in a voice channel of the (already-active) guild."""
    guild = service.bot.get_guild(guild_id)
    channel = guild.add_voice_channel(channel_id)
    guild.members_by_id[user_id] = FakeMember(
        id=user_id, voice=FakeVoiceState(channel=channel)
    )


# ── auth ─────────────────────────────────────────────────────────────────

async def test_missing_token_is_401(client):
    resp = await client.get(f"/control/now-playing?discordUserId={USER_ID}")
    assert resp.status == 401


async def test_wrong_token_is_401(client):
    resp = await client.get(
        f"/control/now-playing?discordUserId={USER_ID}",
        headers={"Authorization": "Bearer nope"},
    )
    assert resp.status == 401


async def test_health_stays_unauthenticated(client):
    resp = await client.get("/health")
    assert resp.status == 200


def test_register_rejects_empty_token(service):
    from aiohttp import web
    from jacky.api.control import register_control_routes

    with pytest.raises(ValueError):
        register_control_routes(
            web.Application(), bot=service.bot, service=service, token=""
        )


# ── session resolution / now-playing ─────────────────────────────────────

async def test_now_playing_inactive_when_user_not_in_voice(client):
    resp = await client.get(f"/control/now-playing?discordUserId={USER_ID}", headers=AUTH)
    assert resp.status == 200
    assert await resp.json() == {"active": False}


async def test_now_playing_inactive_when_bot_has_no_voice_client(client, service, guild_id):
    service.bot.get_guild(guild_id).voice_client = None
    put_user_in_voice(service, guild_id)
    resp = await client.get(f"/control/now-playing?discordUserId={USER_ID}", headers=AUTH)
    assert (await resp.json()) == {"active": False}


async def test_now_playing_reports_current_track(client, service, guild_id, sid):
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {
        "currentTrack": {"title": "Song", "artist": "Artist"},
        "isPaused": False, "volume": 70,
    })
    resp = await client.get(f"/control/now-playing?discordUserId={USER_ID}", headers=AUTH)
    body = await resp.json()
    assert body == {
        "active": True, "title": "Song", "author": "Artist",
        "paused": False, "volume": 70, "guildName": "Guild",
    }


async def test_now_playing_active_but_idle(client, service, guild_id):
    put_user_in_voice(service, guild_id)
    resp = await client.get(f"/control/now-playing?discordUserId={USER_ID}", headers=AUTH)
    body = await resp.json()
    assert body["active"] is True and body["title"] is None


async def test_bad_discord_user_id_is_400(client):
    resp = await client.get("/control/now-playing?discordUserId=notanum", headers=AUTH)
    assert resp.status == 400
    resp = await client.get("/control/now-playing", headers=AUTH)
    assert resp.status == 400


async def test_resolution_picks_guild_where_user_sits_in_voice(client, service, guild_id):
    """Two guilds with live sessions; the user is only in voice in the second."""
    from tests.conftest import FakeGuild, FakeVoice

    other = FakeGuild(id=777, voice_client=FakeVoice(), name="Other")
    service.bot.guilds.insert(0, other)  # scanned first, must NOT match
    await service.repo.init_state("777")
    put_user_in_voice(service, guild_id)
    resp = await client.get(f"/control/now-playing?discordUserId={USER_ID}", headers=AUTH)
    body = await resp.json()
    assert body["guildName"] == "Guild"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/bot && python -m pytest tests/test_control_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jacky.api'`.

- [ ] **Step 3: Implement** — create empty `services/bot/src/jacky/api/__init__.py`, then `services/bot/src/jacky/api/control.py`:

```python
"""Stream Deck control API (spec: 2026-08-06-streamdeck-session-control).

Mounted on the same aiohttp app as /health, so auth is a per-route wrapper,
NOT an app middleware — the guardian polls /health unauthenticated.

Session resolution: the caller sends their Discord user id; the target guild
is the first one where that member is currently in a voice channel AND the
bot holds a voice client (a live session). Same liveness signal
PlayerService.handle_summon uses.
"""

import hmac
import logging
from typing import Any

from aiohttp import web

log = logging.getLogger("jacky.control")


def register_control_routes(
    app: web.Application, *, bot: Any, service: Any, token: str
) -> None:
    if not token:
        raise ValueError("control API requires a non-empty token")
    expected = f"Bearer {token}".encode()

    def guarded(handler):
        async def wrapper(request: web.Request) -> web.Response:
            supplied = request.headers.get("Authorization", "").encode()
            if not hmac.compare_digest(supplied, expected):
                return web.json_response({"error": "unauthorized"}, status=401)
            return await handler(request)
        return wrapper

    def resolve_guild(user_id: int):
        for guild in bot.guilds:
            member = guild.get_member(user_id)
            voice = getattr(member, "voice", None)
            if member and voice and voice.channel and guild.voice_client:
                return guild
        return None

    def parse_user_id(raw) -> int | None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    async def body_of(request: web.Request) -> dict:
        try:
            return await request.json()
        except Exception:  # noqa: BLE001 — malformed body == empty body
            return {}

    async def now_playing(request: web.Request) -> web.Response:
        user_id = parse_user_id(request.query.get("discordUserId"))
        if user_id is None:
            return web.json_response({"error": "bad-discordUserId"}, status=400)
        guild = resolve_guild(user_id)
        if guild is None:
            return web.json_response({"active": False})
        state = await service.repo.get_state(str(guild.id)) or {}
        current = state.get("currentTrack")
        return web.json_response({
            "active": True,
            "title": current.get("title") if current else None,
            "author": current.get("artist", "") if current else "",
            "paused": bool(state.get("isPaused", False)),
            "volume": int(state.get("volume", 80)),
            "guildName": guild.name,
        })

    app.add_routes([web.get("/control/now-playing", guarded(now_playing))])
    log.info("control API routes registered")
```

- [ ] **Step 4: Run to verify passes**

Run: `cd services/bot && python -m pytest tests/test_control_api.py -v`
Expected: PASS (all tests in the file so far).

- [ ] **Step 5: Commit**

```bash
git add services/bot/src/jacky/api/ services/bot/tests/test_control_api.py
git commit -m "feat(control): now-playing route with bearer auth and voice-presence session resolution"
```

### Task 4: Action routes — play-pause, skip, stop, volume

**Files:**
- Modify: `services/bot/src/jacky/api/control.py`
- Test: `services/bot/tests/test_control_api.py`

- [ ] **Step 1: Write the failing tests** — append to `services/bot/tests/test_control_api.py`:

```python
# ── actions ──────────────────────────────────────────────────────────────

async def test_actions_409_without_session(client):
    for path in ("/control/play-pause", "/control/skip",
                 "/control/stop", "/control/volume"):
        resp = await client.post(
            path, json={"discordUserId": USER_ID, "delta": 5}, headers=AUTH
        )
        assert resp.status == 409, path


async def test_play_pause_toggles(client, service, guild_id, sid):
    put_user_in_voice(service, guild_id)
    resp = await client.post(
        "/control/play-pause", json={"discordUserId": USER_ID}, headers=AUTH
    )
    assert resp.status == 200 and (await resp.json()) == {"paused": True}
    assert service.node.updates[-1] == (guild_id, {"paused": True})
    assert (await service.repo.get_state(sid))["isPaused"] is True

    resp = await client.post(
        "/control/play-pause", json={"discordUserId": USER_ID}, headers=AUTH
    )
    assert (await resp.json()) == {"paused": False}
    assert service.node.updates[-1] == (guild_id, {"paused": False})


async def test_skip_stops_current_track(client, service, guild_id):
    put_user_in_voice(service, guild_id)
    resp = await client.post(
        "/control/skip", json={"discordUserId": USER_ID}, headers=AUTH
    )
    assert resp.status == 200
    assert service.node.updates[-1] == (guild_id, {"track": {"encoded": None}})


async def test_stop_tears_down_session(client, service, guild_id, sid):
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {"queue": [{"title": "x"}]})
    resp = await client.post(
        "/control/stop", json={"discordUserId": USER_ID}, headers=AUTH
    )
    assert resp.status == 200
    state = await service.repo.get_state(sid)
    assert state["isPlaying"] is False and state["queue"] == []
    assert service.bot.get_guild(guild_id).voice_client is None or \
        service.bot.get_guild(guild_id).voice_client.disconnected


async def test_volume_applies_delta_and_clamps(client, service, guild_id, sid):
    put_user_in_voice(service, guild_id)
    resp = await client.post(
        "/control/volume", json={"discordUserId": USER_ID, "delta": 5}, headers=AUTH
    )
    assert (await resp.json()) == {"volume": 85}  # init_state volume=80

    await service.repo.update_state(sid, {"volume": 98})
    resp = await client.post(
        "/control/volume", json={"discordUserId": USER_ID, "delta": 5}, headers=AUTH
    )
    assert (await resp.json()) == {"volume": 100}


async def test_volume_missing_delta_is_400(client, service, guild_id):
    put_user_in_voice(service, guild_id)
    resp = await client.post(
        "/control/volume", json={"discordUserId": USER_ID}, headers=AUTH
    )
    assert resp.status == 400
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/bot && python -m pytest tests/test_control_api.py -v`
Expected: new tests FAIL with 404 responses (routes absent); Task 3 tests still PASS.

- [ ] **Step 3: Implement** — in `register_control_routes` (same file), add after `now_playing` and before `app.add_routes`:

```python
    async def action_target(request: web.Request):
        """(guild, body, error_response) triple for POST action routes."""
        body = await body_of(request)
        user_id = parse_user_id(body.get("discordUserId"))
        if user_id is None:
            return None, body, web.json_response(
                {"error": "bad-discordUserId"}, status=400
            )
        guild = resolve_guild(user_id)
        if guild is None:
            return None, body, web.json_response(
                {"error": "no-active-session"}, status=409
            )
        return guild, body, None

    async def play_pause(request: web.Request) -> web.Response:
        guild, _body, err = await action_target(request)
        if err:
            return err
        state = await service.repo.get_state(str(guild.id)) or {}
        new_paused = not state.get("isPaused", False)
        await service.pause(guild.id, new_paused)
        return web.json_response({"paused": new_paused})

    async def skip(request: web.Request) -> web.Response:
        guild, _body, err = await action_target(request)
        if err:
            return err
        await service.skip(guild.id)
        return web.json_response({"ok": True})

    async def stop(request: web.Request) -> web.Response:
        guild, _body, err = await action_target(request)
        if err:
            return err
        await service.teardown_session(guild.id, clear_queue=True)
        return web.json_response({"ok": True})

    async def volume(request: web.Request) -> web.Response:
        guild, body, err = await action_target(request)
        if err:
            return err
        try:
            delta = int(body["delta"])
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "bad-delta"}, status=400)
        state = await service.repo.get_state(str(guild.id)) or {}
        new = await service.set_volume(
            guild.id, int(state.get("volume", 80)) + delta
        )
        return web.json_response({"volume": new})
```

and extend the route table to:

```python
    app.add_routes([
        web.get("/control/now-playing", guarded(now_playing)),
        web.post("/control/play-pause", guarded(play_pause)),
        web.post("/control/skip", guarded(skip)),
        web.post("/control/stop", guarded(stop)),
        web.post("/control/volume", guarded(volume)),
    ])
```

- [ ] **Step 4: Run to verify passes**

Run: `cd services/bot && python -m pytest tests/test_control_api.py -v`
Expected: PASS. Then run the full suite + lint: `python -m pytest -q && ruff check .`
Expected: all PASS, no lint errors.

- [ ] **Step 5: Commit**

```bash
git add services/bot/src/jacky/api/control.py services/bot/tests/test_control_api.py
git commit -m "feat(control): play-pause/skip/stop/volume action routes"
```

### Task 5: Wire into the bot's health server

**Files:**
- Modify: `services/bot/src/jacky/core/health.py`, `services/bot/src/jacky/core/bot.py`

- [ ] **Step 1: Extend `start_health_server`** — in `services/bot/src/jacky/core/health.py` replace the function's first line:

```python
async def start_health_server(
    bot: Any, service: Any, port: int, app: web.Application | None = None
) -> web.AppRunner:
    runner = web.AppRunner(app or build_app(bot, service))
```

(rest of the body unchanged).

- [ ] **Step 2: Register in `setup_hook`** — in `services/bot/src/jacky/core/bot.py`, change the import:

```python
from jacky.core.health import build_app, start_health_server
```

and replace the `self._health_runner = await start_health_server(...)` call at the end of `setup_hook` with:

```python
        health_app = build_app(self, self.service)
        if self.settings.control_api_token:
            from jacky.api.control import register_control_routes

            register_control_routes(
                health_app, bot=self, service=self.service,
                token=self.settings.control_api_token,
            )
        self._health_runner = await start_health_server(
            self, self.service, self.settings.health_port, app=health_app
        )
```

- [ ] **Step 3: Verify**

Run: `cd services/bot && python -m pytest -q && ruff check .`
Expected: all PASS (the `client` fixture already exercises `build_app` + `register_control_routes` together).

- [ ] **Step 4: Commit**

```bash
git add services/bot/src/jacky/core/health.py services/bot/src/jacky/core/bot.py
git commit -m "feat(control): mount control routes on health app when CONTROL_API_TOKEN set"
```

---

## Part 2 — Deploy

### Task 6: Compose + env contract

**Files:**
- Modify: `deploy/docker-compose.yml`, `deploy/.env.example`

- [ ] **Step 1: Bot env passthrough** — in `deploy/docker-compose.yml`, add to the `bot` service `environment:` block:

```yaml
      CONTROL_API_TOKEN: ${CONTROL_API_TOKEN:-}
```

- [ ] **Step 2: cloudflared sidecar** — add after the `token-minter` service (before `networks:`):

```yaml
  cloudflared:
    # Stream Deck control API egress (spec 2026-08-06). Outbound-only named
    # tunnel: control.<domain> -> bot:8080 /control/*, no inbound VM ports.
    # Opt-in via COMPOSE_PROFILES=control in .env — the stack runs without it.
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    profiles: [control]
    command: tunnel --no-autoupdate run
    environment:
      # cloudflared reads TUNNEL_TOKEN natively; empty default keeps plain
      # `docker compose <cmd>` working when the control profile is unused —
      # `:?` in a command is interpolated before profile filtering and would
      # break every compose invocation on token-less deployments.
      TUNNEL_TOKEN: ${CLOUDFLARE_TUNNEL_TOKEN:-}
    depends_on:
      - bot
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "3"
    networks: [jacky]
```

- [ ] **Step 3: Document env vars** — append to `deploy/.env.example`:

```
# ── Stream Deck control API ──────────────────────────────────────────────
# Bearer token for the bot's /control/* REST routes (Stream Deck plugin).
# Empty = routes disabled entirely. Generate: openssl rand -hex 32
CONTROL_API_TOKEN=

# Cloudflare named-tunnel connector token (Zero Trust → Networks → Tunnels
# → your tunnel → Install connector → Docker: the long string after
# `--token`). Public hostname must route control.<your-domain>, path
# `control`, to service http://bot:8080 (see docs/streamdeck-control.md).
CLOUDFLARE_TUNNEL_TOKEN=

# Uncomment to start the cloudflared sidecar with the stack.
#COMPOSE_PROFILES=control
```

- [ ] **Step 4: Validate compose syntax**

Run: `cd deploy && docker compose config --quiet && COMPOSE_PROFILES=control CLOUDFLARE_TUNNEL_TOKEN=dummy docker compose config --quiet`
Expected: both exit 0 (no output). If docker isn't available on this dev machine, skip — the config is exercised on the VM in Task 13.

- [ ] **Step 5: Commit**

```bash
git add deploy/docker-compose.yml deploy/.env.example
git commit -m "chore(deploy): cloudflared control-profile sidecar + control API env contract"
```

### Task 7: Runbook doc

**Files:**
- Create: `docs/streamdeck-control.md`

- [ ] **Step 1: Write the doc** — create `docs/streamdeck-control.md`:

```markdown
# Stream Deck Session Control — Setup & Runbook

Spec: `superpowers/specs/2026-08-06-streamdeck-session-control-design.md`
Plugin source: `streamdeck-plugin/` · Bot API: `services/bot/src/jacky/api/control.py`

## One-time server setup

1. **Token** — on the VM, in `deploy/.env`:
   `CONTROL_API_TOKEN=$(openssl rand -hex 32)` (paste the value, keep it secret).
2. **Cloudflare tunnel** (requires a domain on your Cloudflare account):
   - Zero Trust → Networks → Tunnels → Create tunnel → Cloudflared connector.
   - Copy the Docker connector token into `CLOUDFLARE_TUNNEL_TOKEN` in `deploy/.env`.
   - Public hostname: subdomain `control`, your domain; **Path: `control`**
     (only `/control/*` is forwarded — `/health` stays private); service
     `http://bot:8080` (HTTP — TLS terminates at Cloudflare).
3. **Enable the sidecar** — uncomment `COMPOSE_PROFILES=control` in `deploy/.env`.
4. Redeploy the stack (`make up` on the VM). `docker compose ps` should show
   `cloudflared` running; its logs print `Registered tunnel connection`.

## Verify from anywhere

```bash
curl -s https://control.<your-domain>/control/now-playing?discordUserId=<your-id>
# → {"error": "unauthorized"} (401) — tunnel + routing work
curl -s -H "Authorization: Bearer <token>" \
  "https://control.<your-domain>/control/now-playing?discordUserId=<your-id>"
# → {"active": false} (or track info if you're in a session)
```

Your Discord user ID: Discord → Settings → Advanced → Developer Mode on,
then right-click your name → Copy User ID.

## Install / update the plugin

From `streamdeck-plugin/`: `npm install && npm run build`, then
`npx @elgato/cli pack com.jacobchoi.jacky-control.sdPlugin` and double-click
the produced `.streamDeckPlugin` file (or `npx @elgato/cli link
com.jacobchoi.jacky-control.sdPlugin` for a dev symlink + `npm run watch`).

In the Stream Deck app, drop any Jacky action onto a key and fill the three
settings (shared by all keys): **API URL** `https://control.<your-domain>`,
**API token**, **Discord user ID**.

## Behavior notes

- Keys act on the guild where *you* currently sit in a voice channel with a
  live bot session; nowhere → "No session" / brief ⚠ flash on presses.
- Now Playing polls every 5 s, backing off to 30 s while unreachable.
- Token rotation: new value in `deploy/.env` → `make restart s=bot` → update
  the token in any key's settings.
```

- [ ] **Step 2: Commit**

```bash
git add docs/streamdeck-control.md
git commit -m "docs: Stream Deck control setup runbook"
```

---

## Part 3 — Stream Deck plugin

> Prereq check before Task 8: `node --version` ≥ 20. The Stream Deck app (≥ 6.5) must be installed for Tasks 12–13's link/restart steps, but build + unit tests run without it.

### Task 8: Scaffold

**Files:**
- Create: `streamdeck-plugin/package.json`, `tsconfig.json`, `rollup.config.mjs`, `.gitignore`, `src/plugin.ts`, `com.jacobchoi.jacky-control.sdPlugin/manifest.json`, `.../bin/package.json`, `.../imgs/*.svg`, `.../ui/settings.html`

- [ ] **Step 1: `streamdeck-plugin/package.json`**

```json
{
  "name": "jacky-control",
  "private": true,
  "type": "module",
  "scripts": {
    "build": "rollup -c",
    "watch": "rollup -c -w --watch.onEnd=\"streamdeck restart com.jacobchoi.jacky-control\"",
    "test": "vitest run"
  },
  "dependencies": {
    "@elgato/streamdeck": "^1.3.0"
  },
  "devDependencies": {
    "@rollup/plugin-commonjs": "^28.0.0",
    "@rollup/plugin-node-resolve": "^15.2.3",
    "@rollup/plugin-typescript": "^12.1.0",
    "@tsconfig/node20": "^20.1.4",
    "rollup": "^4.20.0",
    "tslib": "^2.6.3",
    "typescript": "^5.5.4",
    "vitest": "^2.0.5"
  }
}
```

- [ ] **Step 2: `streamdeck-plugin/tsconfig.json`**

```json
{
  "extends": "@tsconfig/node20/tsconfig.json",
  "compilerOptions": {
    "module": "ES2022",
    "moduleResolution": "Bundler",
    "noEmit": true
  },
  "include": ["src", "tests"]
}
```

- [ ] **Step 3: `streamdeck-plugin/rollup.config.mjs`**

```js
import commonjs from "@rollup/plugin-commonjs";
import nodeResolve from "@rollup/plugin-node-resolve";
import typescript from "@rollup/plugin-typescript";

export default {
  input: "src/plugin.ts",
  output: {
    file: "com.jacobchoi.jacky-control.sdPlugin/bin/plugin.js",
    format: "es",
    sourcemap: false,
  },
  plugins: [
    typescript({ noEmit: false, declaration: false }),
    nodeResolve({ preferBuiltins: true, exportConditions: ["node"] }),
    commonjs(),
  ],
};
```

- [ ] **Step 4: `streamdeck-plugin/.gitignore`**

```
node_modules/
com.jacobchoi.jacky-control.sdPlugin/bin/plugin.js
*.streamDeckPlugin
```

- [ ] **Step 5: `streamdeck-plugin/com.jacobchoi.jacky-control.sdPlugin/bin/package.json`** (committed; tells Stream Deck's Node to treat the bundle as ESM)

```json
{ "main": "plugin.js", "type": "module" }
```

- [ ] **Step 6: `streamdeck-plugin/com.jacobchoi.jacky-control.sdPlugin/manifest.json`**

```json
{
  "$schema": "https://schemas.elgato.com/streamdeck/plugins/manifest.json",
  "UUID": "com.jacobchoi.jacky-control",
  "Name": "Jacky Session Control",
  "Description": "Control your Jacky Music Discord session: play/pause, skip, stop, volume, now playing.",
  "Author": "Jacob Choi",
  "Version": "0.1.0.0",
  "Category": "Jacky Music",
  "Icon": "imgs/plugin-icon",
  "CodePath": "bin/plugin.js",
  "SDKVersion": 2,
  "Nodejs": { "Version": "20", "Debug": "disabled" },
  "Software": { "MinimumVersion": "6.5" },
  "OS": [{ "Platform": "windows", "MinimumVersion": "10" }],
  "Actions": [
    {
      "UUID": "com.jacobchoi.jacky-control.play-pause",
      "Name": "Play / Pause",
      "Icon": "imgs/play-pause",
      "Tooltip": "Toggle play/pause for your current session",
      "PropertyInspectorPath": "ui/settings.html",
      "Controllers": ["Keypad"],
      "States": [
        { "Image": "imgs/play-pause", "TitleAlignment": "bottom" },
        { "Image": "imgs/paused", "TitleAlignment": "bottom" }
      ]
    },
    {
      "UUID": "com.jacobchoi.jacky-control.skip",
      "Name": "Skip",
      "Icon": "imgs/skip",
      "Tooltip": "Skip to the next track",
      "PropertyInspectorPath": "ui/settings.html",
      "Controllers": ["Keypad"],
      "States": [{ "Image": "imgs/skip", "TitleAlignment": "bottom" }]
    },
    {
      "UUID": "com.jacobchoi.jacky-control.stop",
      "Name": "Stop",
      "Icon": "imgs/stop",
      "Tooltip": "Stop playback and end the session",
      "PropertyInspectorPath": "ui/settings.html",
      "Controllers": ["Keypad"],
      "States": [{ "Image": "imgs/stop", "TitleAlignment": "bottom" }]
    },
    {
      "UUID": "com.jacobchoi.jacky-control.volume-up",
      "Name": "Volume +",
      "Icon": "imgs/volume-up",
      "Tooltip": "Volume up 5",
      "PropertyInspectorPath": "ui/settings.html",
      "Controllers": ["Keypad"],
      "States": [{ "Image": "imgs/volume-up", "TitleAlignment": "bottom" }]
    },
    {
      "UUID": "com.jacobchoi.jacky-control.volume-down",
      "Name": "Volume −",
      "Icon": "imgs/volume-down",
      "Tooltip": "Volume down 5",
      "PropertyInspectorPath": "ui/settings.html",
      "Controllers": ["Keypad"],
      "States": [{ "Image": "imgs/volume-down", "TitleAlignment": "bottom" }]
    },
    {
      "UUID": "com.jacobchoi.jacky-control.now-playing",
      "Name": "Now Playing",
      "Icon": "imgs/now-playing",
      "Tooltip": "Shows the current track",
      "PropertyInspectorPath": "ui/settings.html",
      "Controllers": ["Keypad"],
      "States": [{ "Image": "imgs/now-playing", "TitleAlignment": "middle" }]
    }
  ]
}
```

- [ ] **Step 7: Icons** — create these SVGs in `streamdeck-plugin/com.jacobchoi.jacky-control.sdPlugin/imgs/`. Shared frame: dark rounded square, accent glyph.

`plugin-icon.svg`:
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><rect width="72" height="72" rx="14" fill="#1a1a2e"/><circle cx="28" cy="46" r="8" fill="#e94560"/><rect x="34" y="18" width="5" height="28" fill="#e94560"/><path d="M34 18 L56 12 V22 L39 27 Z" fill="#e94560"/></svg>
```

`play-pause.svg`:
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><rect width="72" height="72" rx="14" fill="#1a1a2e"/><path d="M20 22 L40 36 L20 50 Z" fill="#e94560"/><rect x="46" y="22" width="6" height="28" fill="#e94560"/><rect x="56" y="22" width="6" height="28" fill="#e94560"/></svg>
```

`paused.svg`:
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><rect width="72" height="72" rx="14" fill="#1a1a2e"/><rect x="24" y="20" width="8" height="32" fill="#f5b942"/><rect x="40" y="20" width="8" height="32" fill="#f5b942"/></svg>
```

`skip.svg`:
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><rect width="72" height="72" rx="14" fill="#1a1a2e"/><path d="M18 22 L40 36 L18 50 Z" fill="#e94560"/><rect x="44" y="22" width="7" height="28" fill="#e94560"/></svg>
```

`stop.svg`:
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><rect width="72" height="72" rx="14" fill="#1a1a2e"/><rect x="22" y="22" width="28" height="28" rx="4" fill="#e94560"/></svg>
```

`volume-up.svg`:
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><rect width="72" height="72" rx="14" fill="#1a1a2e"/><path d="M16 30 H26 L38 20 V52 L26 42 H16 Z" fill="#e94560"/><rect x="46" y="32" width="16" height="6" fill="#e94560"/><rect x="51" y="27" width="6" height="16" fill="#e94560"/></svg>
```

`volume-down.svg`:
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><rect width="72" height="72" rx="14" fill="#1a1a2e"/><path d="M16 30 H26 L38 20 V52 L26 42 H16 Z" fill="#e94560"/><rect x="46" y="32" width="16" height="6" fill="#e94560"/></svg>
```

`now-playing.svg`:
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><rect width="72" height="72" rx="14" fill="#1a1a2e"/><rect x="18" y="30" width="6" height="20" rx="3" fill="#e94560"/><rect x="30" y="20" width="6" height="30" rx="3" fill="#e94560"/><rect x="42" y="26" width="6" height="24" rx="3" fill="#e94560"/><rect x="54" y="34" width="6" height="16" rx="3" fill="#e94560"/></svg>
```

- [ ] **Step 8: Property Inspector** — vendor the sdpi-components library locally (no CDN dependency at runtime, no SRI concern — the packaged plugin must work offline anyway):

Run: `curl -sSL -o "streamdeck-plugin/com.jacobchoi.jacky-control.sdPlugin/ui/sdpi-components.js" https://sdpi-components.dev/releases/v4/sdpi-components.js`
Expected: file exists and is non-trivially sized (`~100 KB`). Commit it with the scaffold — it is a vendored asset, not a build output.

Then create `streamdeck-plugin/com.jacobchoi.jacky-control.sdPlugin/ui/settings.html`:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <script src="sdpi-components.js"></script>
</head>
<body>
  <!-- All three are GLOBAL settings: enter once on any key, shared by all. -->
  <sdpi-item label="API URL">
    <sdpi-textfield global setting="apiUrl" placeholder="https://control.example.com"></sdpi-textfield>
  </sdpi-item>
  <sdpi-item label="API token">
    <sdpi-password global setting="apiToken"></sdpi-password>
  </sdpi-item>
  <sdpi-item label="Discord user ID">
    <sdpi-textfield global setting="discordUserId" placeholder="123456789012345678"></sdpi-textfield>
  </sdpi-item>
</body>
</html>
```

- [ ] **Step 9: Minimal entry point** — `streamdeck-plugin/src/plugin.ts` (actions land in Task 12):

```ts
import streamDeck from "@elgato/streamdeck";

await streamDeck.connect();
```

- [ ] **Step 10: Install & build**

Run: `cd streamdeck-plugin && npm install && npm run build`
Expected: rollup completes; `com.jacobchoi.jacky-control.sdPlugin/bin/plugin.js` exists.

- [ ] **Step 11: Validate the bundle layout**

Run: `cd streamdeck-plugin && npx @elgato/cli@latest validate com.jacobchoi.jacky-control.sdPlugin`
Expected: no errors (warnings about icon sizes are acceptable for personal use). If the validator rejects a manifest field, fix per its message — the schema URL in the manifest gives IDE hints too.

- [ ] **Step 12: Commit**

```bash
git add streamdeck-plugin/
git commit -m "feat(deck): scaffold Jacky Session Control plugin (manifest, PI, icons, build)"
```

### Task 9: Settings + API client

**Files:**
- Create: `streamdeck-plugin/src/settings.ts`, `streamdeck-plugin/src/api-client.ts`
- Test: `streamdeck-plugin/tests/api-client.test.ts`

- [ ] **Step 1: `src/settings.ts`** (types only — no test needed):

```ts
export type GlobalSettings = {
  apiUrl?: string;
  apiToken?: string;
  discordUserId?: string;
};

export function settingsReady(s: GlobalSettings): s is Required<GlobalSettings> {
  return Boolean(s.apiUrl && s.apiToken && s.discordUserId);
}
```

- [ ] **Step 2: Write the failing tests** — `streamdeck-plugin/tests/api-client.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { ControlApiError, JackyClient, type NowPlaying } from "../src/api-client";

const SETTINGS = {
  apiUrl: "https://control.example.com/",
  apiToken: "tok",
  discordUserId: "42",
};

function fetchStub(status: number, body: unknown = {}) {
  return vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  })) as unknown as typeof fetch;
}

describe("JackyClient", () => {
  it("POSTs actions with auth header, user id, and trimmed base URL", async () => {
    const f = fetchStub(200);
    await new JackyClient(SETTINGS, f).volume(5);
    const [url, init] = (f as any).mock.calls[0];
    expect(url).toBe("https://control.example.com/control/volume");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(JSON.parse(init.body)).toEqual({ discordUserId: "42", delta: 5 });
  });

  it("GETs now-playing with the user id in the query", async () => {
    const data: NowPlaying = { active: false };
    const f = fetchStub(200, data);
    const result = await new JackyClient(SETTINGS, f).nowPlaying();
    const [url] = (f as any).mock.calls[0];
    expect(url).toBe(
      "https://control.example.com/control/now-playing?discordUserId=42",
    );
    expect(result).toEqual({ active: false });
  });

  it("throws ControlApiError carrying the status on non-2xx", async () => {
    const client = new JackyClient(SETTINGS, fetchStub(401));
    await expect(client.playPause()).rejects.toMatchObject({ status: 401 });
    await expect(client.playPause()).rejects.toBeInstanceOf(ControlApiError);
  });

  it("exposes one method per route", async () => {
    const f = fetchStub(200);
    const client = new JackyClient(SETTINGS, f);
    await client.playPause();
    await client.skip();
    await client.stop();
    const urls = (f as any).mock.calls.map((c: any[]) => c[0]);
    expect(urls).toEqual([
      "https://control.example.com/control/play-pause",
      "https://control.example.com/control/skip",
      "https://control.example.com/control/stop",
    ]);
  });
});
```

- [ ] **Step 3: Run to verify failure**

Run: `cd streamdeck-plugin && npm test`
Expected: FAIL — cannot resolve `../src/api-client`.

- [ ] **Step 4: Implement** — `streamdeck-plugin/src/api-client.ts`:

```ts
import type { GlobalSettings } from "./settings";

export type NowPlaying =
  | { active: false }
  | {
      active: true;
      title: string | null;
      author: string;
      paused: boolean;
      volume: number;
      guildName: string;
    };

export class ControlApiError extends Error {
  constructor(readonly status: number) {
    super(`control api responded ${status}`);
  }
}

export class JackyClient {
  constructor(
    private readonly s: Required<GlobalSettings>,
    private readonly fetchFn: typeof fetch = fetch,
  ) {}

  private url(path: string): string {
    return this.s.apiUrl.replace(/\/+$/, "") + path;
  }

  private async post(path: string, extra: Record<string, unknown> = {}): Promise<void> {
    const res = await this.fetchFn(this.url(path), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.s.apiToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ discordUserId: this.s.discordUserId, ...extra }),
    });
    if (!res.ok) throw new ControlApiError(res.status);
  }

  playPause(): Promise<void> {
    return this.post("/control/play-pause");
  }

  skip(): Promise<void> {
    return this.post("/control/skip");
  }

  stop(): Promise<void> {
    return this.post("/control/stop");
  }

  volume(delta: number): Promise<void> {
    return this.post("/control/volume", { delta });
  }

  async nowPlaying(): Promise<NowPlaying> {
    const query = `?discordUserId=${encodeURIComponent(this.s.discordUserId)}`;
    const res = await this.fetchFn(this.url("/control/now-playing") + query, {
      headers: { Authorization: `Bearer ${this.s.apiToken}` },
    });
    if (!res.ok) throw new ControlApiError(res.status);
    return (await res.json()) as NowPlaying;
  }
}
```

- [ ] **Step 5: Run to verify passes** — `npm test`, expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add streamdeck-plugin/src/settings.ts streamdeck-plugin/src/api-client.ts streamdeck-plugin/tests/api-client.test.ts
git commit -m "feat(deck): typed control-API client with bearer auth"
```

### Task 10: Marquee formatter

**Files:**
- Create: `streamdeck-plugin/src/format.ts`
- Test: `streamdeck-plugin/tests/format.test.ts`

- [ ] **Step 1: Write the failing tests** — `streamdeck-plugin/tests/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { marquee } from "../src/format";

describe("marquee", () => {
  it("returns short titles unchanged", () => {
    expect(marquee("Short", 0, 9)).toBe("Short");
    expect(marquee("Short", 7, 9)).toBe("Short");
  });

  it("windows long titles from the offset", () => {
    expect(marquee("A Very Long Song Title", 0, 9)).toBe("A Very Lo");
    expect(marquee("A Very Long Song Title", 2, 9)).toBe("Very Long");
  });

  it("wraps around with a gap after the end", () => {
    const t = "ABCDEF"; // padded loop: "ABCDEF   " (len 9)
    expect(marquee(t, 5, 4, 3)).toBe("F   ");
    expect(marquee(t, 8, 4, 3)).toBe(" ABC");
    expect(marquee(t, 9, 4, 3)).toBe(marquee(t, 0, 4, 3)); // full cycle
  });
});
```

(The third test passes `width` 4 so `text.length (6) > width` forces scrolling.)

- [ ] **Step 2: Run to verify failure** — `npm test`, expected: FAIL, module not found.

- [ ] **Step 3: Implement** — `streamdeck-plugin/src/format.ts`:

```ts
/** Fixed-width scrolling window over a title; offset advances per poll tick. */
export function marquee(
  text: string,
  offset: number,
  width: number,
  gap = 3,
): string {
  if (text.length <= width) return text;
  const looped = text + " ".repeat(gap);
  const start = offset % looped.length;
  return (looped + looped).slice(start, start + width);
}
```

- [ ] **Step 4: Run to verify passes** — `npm test`, expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add streamdeck-plugin/src/format.ts streamdeck-plugin/tests/format.test.ts
git commit -m "feat(deck): marquee title formatter"
```

### Task 11: Session poller

**Files:**
- Create: `streamdeck-plugin/src/poller.ts`
- Test: `streamdeck-plugin/tests/poller.test.ts`

- [ ] **Step 1: Write the failing tests** — `streamdeck-plugin/tests/poller.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ControlApiError } from "../src/api-client";
import { SessionPoller, type PollState } from "../src/poller";

describe("SessionPoller", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function collect() {
    const states: PollState[] = [];
    return { states, cb: (s: PollState) => states.push(s) };
  }

  it("polls immediately on first subscribe and repeats at the base interval", async () => {
    const poll = vi.fn(async () => ({ active: false }) as const);
    const poller = new SessionPoller(poll, 5000, 30000);
    const { states, cb } = collect();
    poller.subscribe(cb);
    await vi.advanceTimersByTimeAsync(0);
    expect(poll).toHaveBeenCalledTimes(1);
    expect(states[0]).toEqual({ kind: "data", data: { active: false } });
    await vi.advanceTimersByTimeAsync(5000);
    expect(poll).toHaveBeenCalledTimes(2);
    poller.unsubscribe(cb);
  });

  it("stops polling when the last subscriber leaves", async () => {
    const poll = vi.fn(async () => ({ active: false }) as const);
    const poller = new SessionPoller(poll, 5000, 30000);
    const { cb } = collect();
    poller.subscribe(cb);
    await vi.advanceTimersByTimeAsync(0);
    poller.unsubscribe(cb);
    await vi.advanceTimersByTimeAsync(60000);
    expect(poll).toHaveBeenCalledTimes(1);
  });

  it("classifies failures and backs off after 3 consecutive, recovering on success", async () => {
    let failing = true;
    const poll = vi.fn(async () => {
      if (failing) throw new ControlApiError(500);
      return { active: false } as const;
    });
    const poller = new SessionPoller(poll, 5000, 30000);
    const { states, cb } = collect();
    poller.subscribe(cb);
    await vi.advanceTimersByTimeAsync(0);      // failure 1
    await vi.advanceTimersByTimeAsync(5000);   // failure 2
    await vi.advanceTimersByTimeAsync(5000);   // failure 3 -> backoff engaged
    expect(states.every((s) => s.kind === "offline")).toBe(true);
    await vi.advanceTimersByTimeAsync(5000);   // base interval: nothing (backing off)
    expect(poll).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(25000);  // 30s total since failure 3
    expect(poll).toHaveBeenCalledTimes(4);
    failing = false;
    await vi.advanceTimersByTimeAsync(30000);  // still backed off for this tick
    expect(states.at(-1)).toEqual({ kind: "data", data: { active: false } });
    await vi.advanceTimersByTimeAsync(5000);   // recovered -> base interval again
    expect(poll).toHaveBeenCalledTimes(6);
    poller.unsubscribe(cb);
  });

  it("maps 401 to unauthorized and status 0 to unconfigured", async () => {
    const poller401 = new SessionPoller(async () => {
      throw new ControlApiError(401);
    }, 5000, 30000);
    const a = collect();
    poller401.subscribe(a.cb);
    await vi.advanceTimersByTimeAsync(0);
    expect(a.states[0]).toEqual({ kind: "unauthorized" });
    poller401.unsubscribe(a.cb);

    const poller0 = new SessionPoller(async () => {
      throw new ControlApiError(0);
    }, 5000, 30000);
    const b = collect();
    poller0.subscribe(b.cb);
    await vi.advanceTimersByTimeAsync(0);
    expect(b.states[0]).toEqual({ kind: "unconfigured" });
    poller0.unsubscribe(b.cb);
  });
});
```

- [ ] **Step 2: Run to verify failure** — `npm test`, expected: FAIL, module not found.

- [ ] **Step 3: Implement** — `streamdeck-plugin/src/poller.ts`:

```ts
import { ControlApiError, type NowPlaying } from "./api-client";

export type PollState =
  | { kind: "data"; data: NowPlaying }
  | { kind: "offline" }
  | { kind: "unauthorized" }
  | { kind: "unconfigured" };

const BACKOFF_AFTER_FAILURES = 3;

/** Single shared now-playing poll loop. Runs only while subscribed; backs
 *  off from baseMs to maxMs after consecutive failures. */
export class SessionPoller {
  private readonly subs = new Set<(s: PollState) => void>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private failures = 0;

  constructor(
    private readonly poll: () => Promise<NowPlaying>,
    private readonly baseMs = 5000,
    private readonly maxMs = 30000,
  ) {}

  subscribe(cb: (s: PollState) => void): void {
    this.subs.add(cb);
    if (this.subs.size === 1) void this.tick();
  }

  unsubscribe(cb: (s: PollState) => void): void {
    this.subs.delete(cb);
    if (this.subs.size === 0 && this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  private emit(s: PollState): void {
    for (const cb of this.subs) cb(s);
  }

  private async tick(): Promise<void> {
    this.timer = null;
    try {
      const data = await this.poll();
      this.failures = 0;
      this.emit({ kind: "data", data });
    } catch (err) {
      this.failures += 1;
      if (err instanceof ControlApiError && err.status === 401) {
        this.emit({ kind: "unauthorized" });
      } else if (err instanceof ControlApiError && err.status === 0) {
        this.emit({ kind: "unconfigured" });
      } else {
        this.emit({ kind: "offline" });
      }
    }
    if (this.subs.size > 0) {
      const delay = this.failures >= BACKOFF_AFTER_FAILURES ? this.maxMs : this.baseMs;
      this.timer = setTimeout(() => void this.tick(), delay);
    }
  }
}
```

- [ ] **Step 4: Run to verify passes** — `npm test`, expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add streamdeck-plugin/src/poller.ts streamdeck-plugin/tests/poller.test.ts
git commit -m "feat(deck): refcounted session poller with failure backoff"
```

### Task 12: Runtime, actions, registration

**Files:**
- Create: `streamdeck-plugin/src/runtime.ts`, `streamdeck-plugin/src/actions/{play-pause,skip,stop,volume-up,volume-down,now-playing}.ts`
- Modify: `streamdeck-plugin/src/plugin.ts`

These are thin Stream Deck I/O shells over the tested modules — no unit tests; Task 13 verifies them live.

- [ ] **Step 1: `src/runtime.ts`**

```ts
import streamDeck from "@elgato/streamdeck";
import { ControlApiError, JackyClient } from "./api-client";
import { SessionPoller } from "./poller";
import { settingsReady, type GlobalSettings } from "./settings";

let client: JackyClient | null = null;

export function getClient(): JackyClient | null {
  return client;
}

export const poller = new SessionPoller(async () => {
  if (!client) throw new ControlApiError(0); // -> "unconfigured"
  return client.nowPlaying();
});

/** Load global settings and rebuild the client whenever they change. */
export async function initRuntime(): Promise<void> {
  const apply = (s: GlobalSettings) => {
    client = settingsReady(s) ? new JackyClient(s) : null;
  };
  streamDeck.settings.onDidReceiveGlobalSettings<GlobalSettings>((ev) =>
    apply(ev.settings),
  );
  apply(await streamDeck.settings.getGlobalSettings<GlobalSettings>());
}
```

- [ ] **Step 2: `src/actions/play-pause.ts`**

```ts
import { action, SingletonAction, type KeyDownEvent, type WillAppearEvent, type WillDisappearEvent } from "@elgato/streamdeck";
import { getClient, poller } from "../runtime";
import type { PollState } from "../poller";

@action({ UUID: "com.jacobchoi.jacky-control.play-pause" })
export class PlayPause extends SingletonAction {
  private visible = 0;

  private readonly onPoll = (s: PollState): void => {
    if (s.kind !== "data" || !s.data.active) return;
    const state = s.data.paused ? 1 : 0; // manifest state 1 = paused icon
    for (const a of this.actions) {
      if (a.isKey()) void a.setState(state);
    }
  };

  override onWillAppear(_ev: WillAppearEvent): void {
    if (++this.visible === 1) poller.subscribe(this.onPoll);
  }

  override onWillDisappear(_ev: WillDisappearEvent): void {
    if (--this.visible === 0) poller.unsubscribe(this.onPoll);
  }

  override async onKeyDown(ev: KeyDownEvent): Promise<void> {
    const client = getClient();
    if (!client) return ev.action.showAlert();
    try {
      await client.playPause();
      await ev.action.showOk();
    } catch {
      await ev.action.showAlert();
    }
  }
}
```

- [ ] **Step 3: Fire-and-forget actions** — `src/actions/skip.ts`:

```ts
import { action, SingletonAction, type KeyDownEvent } from "@elgato/streamdeck";
import { getClient } from "../runtime";

@action({ UUID: "com.jacobchoi.jacky-control.skip" })
export class Skip extends SingletonAction {
  override async onKeyDown(ev: KeyDownEvent): Promise<void> {
    const client = getClient();
    if (!client) return ev.action.showAlert();
    try {
      await client.skip();
      await ev.action.showOk();
    } catch {
      await ev.action.showAlert();
    }
  }
}
```

`src/actions/stop.ts`:

```ts
import { action, SingletonAction, type KeyDownEvent } from "@elgato/streamdeck";
import { getClient } from "../runtime";

@action({ UUID: "com.jacobchoi.jacky-control.stop" })
export class Stop extends SingletonAction {
  override async onKeyDown(ev: KeyDownEvent): Promise<void> {
    const client = getClient();
    if (!client) return ev.action.showAlert();
    try {
      await client.stop();
      await ev.action.showOk();
    } catch {
      await ev.action.showAlert();
    }
  }
}
```

`src/actions/volume-up.ts`:

```ts
import { action, SingletonAction, type KeyDownEvent } from "@elgato/streamdeck";
import { getClient } from "../runtime";

@action({ UUID: "com.jacobchoi.jacky-control.volume-up" })
export class VolumeUp extends SingletonAction {
  override async onKeyDown(ev: KeyDownEvent): Promise<void> {
    const client = getClient();
    if (!client) return ev.action.showAlert();
    try {
      await client.volume(5);
      await ev.action.showOk();
    } catch {
      await ev.action.showAlert();
    }
  }
}
```

`src/actions/volume-down.ts`:

```ts
import { action, SingletonAction, type KeyDownEvent } from "@elgato/streamdeck";
import { getClient } from "../runtime";

@action({ UUID: "com.jacobchoi.jacky-control.volume-down" })
export class VolumeDown extends SingletonAction {
  override async onKeyDown(ev: KeyDownEvent): Promise<void> {
    const client = getClient();
    if (!client) return ev.action.showAlert();
    try {
      await client.volume(-5);
      await ev.action.showOk();
    } catch {
      await ev.action.showAlert();
    }
  }
}
```

- [ ] **Step 4: `src/actions/now-playing.ts`**

```ts
import { action, SingletonAction, type WillAppearEvent, type WillDisappearEvent } from "@elgato/streamdeck";
import { marquee } from "../format";
import type { PollState } from "../poller";
import { poller } from "../runtime";

const TITLE_WIDTH = 9;

@action({ UUID: "com.jacobchoi.jacky-control.now-playing" })
export class NowPlaying extends SingletonAction {
  private visible = 0;
  private offset = 0;

  private readonly onPoll = (s: PollState): void => {
    let text: string;
    if (s.kind === "unconfigured") text = "Setup\nneeded";
    else if (s.kind === "unauthorized") text = "Auth\nerror";
    else if (s.kind === "offline") text = "Offline";
    else if (!s.data.active) text = "No\nsession";
    else if (!s.data.title) text = `${s.data.guildName}\n(idle)`;
    else {
      text = marquee(s.data.title, this.offset, TITLE_WIDTH);
      if (s.data.paused) text += "\n⏸";
      this.offset += 2;
    }
    for (const a of this.actions) void a.setTitle(text);
  };

  override onWillAppear(_ev: WillAppearEvent): void {
    if (++this.visible === 1) {
      this.offset = 0;
      poller.subscribe(this.onPoll);
    }
  }

  override onWillDisappear(_ev: WillDisappearEvent): void {
    if (--this.visible === 0) poller.unsubscribe(this.onPoll);
  }
}
```

- [ ] **Step 5: Registration** — replace `src/plugin.ts`:

```ts
import streamDeck from "@elgato/streamdeck";
import { NowPlaying } from "./actions/now-playing";
import { PlayPause } from "./actions/play-pause";
import { Skip } from "./actions/skip";
import { Stop } from "./actions/stop";
import { VolumeDown } from "./actions/volume-down";
import { VolumeUp } from "./actions/volume-up";
import { initRuntime } from "./runtime";

streamDeck.actions.registerAction(new PlayPause());
streamDeck.actions.registerAction(new Skip());
streamDeck.actions.registerAction(new Stop());
streamDeck.actions.registerAction(new VolumeUp());
streamDeck.actions.registerAction(new VolumeDown());
streamDeck.actions.registerAction(new NowPlaying());

await streamDeck.connect();
await initRuntime();
```

(If the SDK version in use lacks `a.isKey()` on visible actions or typed `onDidReceiveGlobalSettings`, adapt to the installed `@elgato/streamdeck` d.ts — the compiled build in the next step is the arbiter.)

- [ ] **Step 6: Build + tests**

Run: `cd streamdeck-plugin && npm run build && npm test`
Expected: clean build, all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add streamdeck-plugin/src/
git commit -m "feat(deck): six key actions wired to the control API"
```

### Task 13: Live install, end-to-end, pack

Manual — needs the Stream Deck app, the deployed bot, and the tunnel (server steps from `docs/streamdeck-control.md` Tasks 6–7 must be done on the VM first).

- [ ] **Step 1: Dev-link the plugin**

Run: `cd streamdeck-plugin && npx @elgato/cli@latest link com.jacobchoi.jacky-control.sdPlugin && npx @elgato/cli@latest restart com.jacobchoi.jacky-control`
Expected: six "Jacky Music" actions appear in the Stream Deck app's action list.

- [ ] **Step 2: Configure** — drop Now Playing on a key, open its inspector, fill API URL / token / Discord user ID.

- [ ] **Step 3: Walkthrough** (with a session running: join voice, `j!play something`):
  - Now Playing shows the track title scrolling; pause via `j!pause` → ⏸ appears within ~5 s.
  - Play/Pause key toggles and its icon flips state.
  - Skip advances the queue; Volume ± changes `j!volume`-visible level by 5; Stop ends the session (bot leaves voice).
  - Leave voice yourself → keys 409: Now Playing shows "No session", presses flash ⚠.
  - Stop the tunnel container (`docker compose stop cloudflared` on the VM) → "Offline" within ~15 s; restart it → recovers.

- [ ] **Step 4: Pack the installable**

Run: `cd streamdeck-plugin && npx @elgato/cli@latest pack com.jacobchoi.jacky-control.sdPlugin`
Expected: `com.jacobchoi.jacky-control.streamDeckPlugin` produced (git-ignored). Double-clicking it installs on any machine — this is the "published for personal use" artifact.

- [ ] **Step 5: Final full check + commit any fixes**

Run: `cd services/bot && python -m pytest -q && ruff check .` and `cd streamdeck-plugin && npm test`
Expected: everything green.

```bash
git add -A streamdeck-plugin/ services/bot/ docs/
git commit -m "feat(deck): live-tested Stream Deck session control v0.1"
```
