# Stability Rewrite — M2 Audio Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the poToken auth layer real: a pot-provider sidecar + token-minter service that mint and push fresh poToken/visitorData to Lavalink at runtime and persist them for cold starts, plus a Lavalink healthcheck and a v2-native OAuth reauth flow.

**Architecture:** Two new compose services. `pot-provider` is the stock `brainicism/bgutil-ytdlp-pot-provider` image (Node, no Chromium — supersedes the spec §3.3 assumption of a headless-Chromium one-shot; recorded as ADR-0004). `token-minter` is our Python service (same shape as bot/guardian): every N hours it asks pot-provider for a fresh poToken+visitorData, POSTs them to Lavalink's `/youtube` route (204 = accepted, no restart needed), and atomically writes `tokens.env` to the shared volume, which the Lavalink entrypoint injects at cold start. Spec: `docs/superpowers/specs/2026-07-02-stability-rewrite-design.md` §3.2–3.3.

**Verified facts (do not re-research):**
- youtube-source runtime API: `POST /youtube` with JSON body — any subset of `{"refreshToken", "skipInitialization", "poToken", "visitorData"}` — auth via the standard Lavalink `Authorization` header; `204 No Content` on success. `GET /youtube` returns `{"refreshToken": ...}`.
- Static config keys: `plugins.youtube.pot.token` / `plugins.youtube.pot.visitorData`.
- poToken applies only to the `WEB` & `WEBEMBEDDED` clients (our client order already fronts `TVHTML5_SIMPLY`; poToken widens the fallback net, it does not replace it).
- OAuth device flow: with `oauth.enabled: true` and an empty refresh token, the plugin logs a device code; on approval it logs the refresh token — visible only if logger `dev.lavalink.youtube.http.YoutubeOauth2Handler` is at INFO.
- bgutil pot-provider: image `brainicism/bgutil-ytdlp-pot-provider` (`:node` tag), HTTP server on port 4416, run with init process; `TOKEN_TTL` env (hours, default 6). Its exact endpoint schema is NOT documented — Task 1 pins it empirically.

**Tech Stack:** Python 3.11 + aiohttp (minter), pytest + pytest-asyncio, Docker Compose, Lavalink v4.

**PR map (each PR = one GitHub issue):**
| PR | Tasks | Branch |
|----|-------|--------|
| PR-A token-minter service + ADR-0004 | 1–5 | `feat/m2-token-minter` |
| PR-B compose wiring + lavalink cold-start injection + healthcheck | 6–8 | `feat/m2-compose-wiring` |
| PR-C v2 reauth flow + docs | 9–10 | `feat/m2-reauth-docs` |

**Merge order note (learned in M1):** if PRs are stacked, merge the TOP of the stack first (C → B → A), or simply merge each into master sequentially after its base merges. Never merge a base branch before its children have merged into it.

---

### Task 1: Spike — pin the pot-provider contract (ADR-0004)

**Files:**
- Create: `docs/architecture/decisions/0004-bgutil-pot-provider.md`

- [ ] **Step 1: Probe the container empirically**

```bash
docker run -d --init --name pot-spike -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider:node
sleep 8 && docker logs pot-spike
# Probe likely endpoints (the yt-dlp GetPOT protocol uses POST /get_pot):
curl -s -X POST http://localhost:4416/get_pot -H "Content-Type: application/json" -d '{}' | head -c 2000; echo
curl -s http://localhost:4416/ping; echo
```
Record: exact endpoint path(s), request body fields (does an empty/absent `content_binding` make it generate its own visitor data?), response JSON field names (`po_token` vs `poToken`, `visitor_data` vs `visitorData` vs `content_binding`), and approximate response time. If `/get_pot` 404s, inspect the server source inside the image (`docker exec pot-spike ls /app; docker exec pot-spike cat /app/build/main.js | grep -oE '"/[a-z_]+"' | sort -u`) to find the routes.

- [ ] **Step 2: Verify the minted token is pushable to Lavalink**

```bash
docker run -d --rm --name ll-spike -e YOUTUBE_PLUGIN_VERSION=<current from deploy/.env.example> -e LAVALINK_SERVER_PASSWORD=spike jacky-lavalink:dev 2>/dev/null || docker build -t jacky-lavalink:dev services/lavalink && docker run -d --rm --name ll-spike -e YOUTUBE_PLUGIN_VERSION=<same> -e LAVALINK_SERVER_PASSWORD=spike jacky-lavalink:dev
sleep 20
LL_IP=$(docker inspect ll-spike --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
curl -s -o /dev/null -w "%{http_code}\n" -X POST "http://$LL_IP:2333/youtube" -H "Authorization: spike" -H "Content-Type: application/json" -d '{"poToken":"<token from step 1>","visitorData":"<visitorData from step 1>"}'
```
Expected: `204`. Also record whether the lavalink base image has `curl` or `wget` available (`docker exec ll-spike sh -c 'command -v curl wget'`) — Task 7's healthcheck depends on it. Clean up both containers.

- [ ] **Step 3: Write ADR-0004 and commit**

`docs/architecture/decisions/0004-bgutil-pot-provider.md`:
```markdown
# ADR-0004: bgutil pot-provider replaces the Chromium session generator

**Status:** Accepted · 2026-07-03

## Context
The design spec (§3.3) assumed a headless-Chromium "trusted session
generator" one-shot (~400MB spikes). That tool (iv-org) is deprecated and
unreliable against current YouTube. The actively maintained alternative,
bgutil-ytdlp-pot-provider, solves the BotGuard attestation in Node without
a browser.

## Decision
Run the stock `brainicism/bgutil-ytdlp-pot-provider:node` image as an
always-on sidecar (`pot-provider`, internal port 4416), and our own small
Python `token-minter` service that periodically requests tokens from it,
pushes them to Lavalink's `POST /youtube`, and persists them to the tokens
volume for cold starts.

## Pinned contract (empirical, <date>)
<fill in from Steps 1–2: endpoint, request, response field names, timing,
whether lavalink image has curl/wget>

## Consequences
(+) No Chromium: steady-state RAM ~<measured>MB instead of 400MB spikes;
stock image means upstream maintains the BotGuard arms race, not us.
(−) One more always-on container; the provider's API is pinned by this ADR
rather than upstream docs — if a bgutil upgrade changes it, the minter's
contract tests catch it.
```
Fill every `<...>` with the measured values — an ADR with placeholders is a failed task.

```bash
git add docs/architecture/decisions/0004-bgutil-pot-provider.md
git commit -m "docs(adr): pin bgutil pot-provider contract (supersedes chromium generator)"
```

---

### Task 2: token-minter package scaffold

**Files:**
- Create: `services/token-minter/pyproject.toml`
- Create: `services/token-minter/src/minter/__init__.py`
- Create: `services/token-minter/tests/test_smoke.py`

- [ ] **Step 1: Failing test** — `services/token-minter/tests/test_smoke.py`:
```python
import minter


def test_package_has_version() -> None:
    assert minter.__version__ == "2.0.0"
```

- [ ] **Step 2:** `cd services/token-minter && python -m pytest tests/ -v` → ModuleNotFoundError.

- [ ] **Step 3:** `services/token-minter/pyproject.toml` (same shape as bot/guardian, plus aiohttp):
```toml
[project]
name = "jacky-token-minter"
version = "2.0.0"
description = "Mints poToken/visitorData via pot-provider and pushes them to Lavalink"
requires-python = ">=3.11"
dependencies = ["aiohttp>=3.9"]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "ruff>=0.4"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
src = ["src"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
```
`services/token-minter/src/minter/__init__.py`:
```python
__version__ = "2.0.0"
```

- [ ] **Step 4:** `pip install -e ".[dev]" && python -m pytest tests/ -v` → 1 passed.

- [ ] **Step 5:** `git add services/token-minter && git commit -m "feat(minter): scaffold jacky-token-minter package"`

---

### Task 3: minter settings + runtime

**Files:**
- Create: `services/token-minter/src/minter/config.py`
- Create: `services/token-minter/src/minter/core/__init__.py` (empty)
- Create: `services/token-minter/src/minter/core/runtime.py` (byte-identical copy of `services/bot/src/jacky/core/runtime.py` — the deliberate ADR-0003 duplication)
- Test: `services/token-minter/tests/test_config.py`

- [ ] **Step 1: Failing test** — `services/token-minter/tests/test_config.py`:
```python
import pytest

from minter.config import Settings


def test_from_env_reads_required_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POT_PROVIDER_URL", "http://pot-provider:4416/")
    monkeypatch.setenv("LAVALINK_HOST", "lavalink")
    monkeypatch.setenv("LAVALINK_PORT", "2333")
    monkeypatch.setenv("LAVALINK_PASSWORD", "hunter2")
    monkeypatch.delenv("TOKENS_FILE", raising=False)
    monkeypatch.delenv("POT_REFRESH_HOURS", raising=False)
    s = Settings.from_env()
    assert s.pot_provider_url == "http://pot-provider:4416"  # trailing slash stripped
    assert s.lavalink_url == "http://lavalink:2333"
    assert s.lavalink_password == "hunter2"
    assert s.tokens_file == "/data/tokens/tokens.env"
    assert s.refresh_hours == 6.0


def test_from_env_missing_required_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POT_PROVIDER_URL", raising=False)
    with pytest.raises(KeyError):
        Settings.from_env()
```

- [ ] **Step 2:** run → ModuleNotFoundError. 

- [ ] **Step 3: Implement** — `services/token-minter/src/minter/config.py`:
```python
"""Environment-driven settings. Fail fast on missing required vars."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    pot_provider_url: str
    lavalink_url: str
    lavalink_password: str
    tokens_file: str
    refresh_hours: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            pot_provider_url=os.environ["POT_PROVIDER_URL"].rstrip("/"),
            lavalink_url=f"http://{os.environ['LAVALINK_HOST']}:{os.environ['LAVALINK_PORT']}",
            lavalink_password=os.environ["LAVALINK_PASSWORD"],
            tokens_file=os.environ.get("TOKENS_FILE", "/data/tokens/tokens.env"),
            refresh_hours=float(os.environ.get("POT_REFRESH_HOURS", "6")),
        )
```
Copy runtime.py from the bot (verify identical: `git diff --no-index services/bot/src/jacky/core/runtime.py services/token-minter/src/minter/core/runtime.py` → empty).

- [ ] **Step 4:** `python -m pytest tests/ -v` → 3 passed.

- [ ] **Step 5:** `git add services/token-minter && git commit -m "feat(minter): settings from env + shared runtime helper"`

---

### Task 4: mint/push/persist core (TDD)

**Files:**
- Create: `services/token-minter/src/minter/mint.py`
- Test: `services/token-minter/tests/test_mint.py`

**IMPORTANT:** The pot-provider request/response field names below assume the yt-dlp GetPOT convention (`POST /get_pot`, empty JSON body → provider generates its own visitor data; response `{"po_token": ...}` plus visitor data). If ADR-0004's pinned contract differs, use the ADR's field names in BOTH the code and the tests — the ADR is the source of truth, and the fake server in the tests must mimic the real provider exactly.

- [ ] **Step 1: Failing tests** — `services/token-minter/tests/test_mint.py`:
```python
import asyncio

import aiohttp
import pytest
from aiohttp import web

from minter.mint import MintError, fetch_tokens, push_to_lavalink, write_tokens_file


@pytest.fixture
async def serve():
    runners: list[web.AppRunner] = []

    async def start(routes) -> str:
        app = web.Application()
        app.add_routes(routes)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        runners.append(runner)
        port = site._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    yield start
    for r in runners:
        await r.cleanup()


async def test_fetch_tokens_happy_path(serve) -> None:
    async def get_pot(request: web.Request) -> web.Response:
        assert await request.json() == {}
        return web.json_response({"po_token": "PO123", "visitor_data": "VD456"})

    url = await serve([web.post("/get_pot", get_pot)])
    async with aiohttp.ClientSession() as session:
        po, vd = await fetch_tokens(session, url)
    assert (po, vd) == ("PO123", "VD456")


async def test_fetch_tokens_http_error_raises(serve) -> None:
    async def get_pot(request: web.Request) -> web.Response:
        return web.Response(status=500)

    url = await serve([web.post("/get_pot", get_pot)])
    async with aiohttp.ClientSession() as session:
        with pytest.raises(MintError):
            await fetch_tokens(session, url)


async def test_fetch_tokens_missing_fields_raises(serve) -> None:
    async def get_pot(request: web.Request) -> web.Response:
        return web.json_response({"unexpected": True})

    url = await serve([web.post("/get_pot", get_pot)])
    async with aiohttp.ClientSession() as session:
        with pytest.raises(MintError):
            await fetch_tokens(session, url)


async def test_push_sends_auth_and_accepts_204(serve) -> None:
    seen: dict = {}

    async def youtube(request: web.Request) -> web.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = await request.json()
        return web.Response(status=204)

    url = await serve([web.post("/youtube", youtube)])
    async with aiohttp.ClientSession() as session:
        await push_to_lavalink(session, url, "hunter2", "PO123", "VD456")
    assert seen["auth"] == "hunter2"
    assert seen["body"] == {"poToken": "PO123", "visitorData": "VD456"}


async def test_push_non_204_raises(serve) -> None:
    async def youtube(request: web.Request) -> web.Response:
        return web.Response(status=401)

    url = await serve([web.post("/youtube", youtube)])
    async with aiohttp.ClientSession() as session:
        with pytest.raises(MintError):
            await push_to_lavalink(session, url, "wrong", "PO123", "VD456")


def test_write_tokens_file_atomic(tmp_path) -> None:
    path = tmp_path / "sub" / "tokens.env"
    write_tokens_file(str(path), "PO+abc/123=", "VD_def-456")
    assert path.read_text() == "POT_TOKEN=PO+abc/123=\nPOT_VISITOR_DATA=VD_def-456\n"
    assert not path.with_suffix(".env.tmp").exists()


def test_write_tokens_file_rejects_unsafe_values(tmp_path) -> None:
    with pytest.raises(MintError):
        write_tokens_file(str(tmp_path / "t.env"), "evil\ntoken", "VD")
    with pytest.raises(MintError):
        write_tokens_file(str(tmp_path / "t.env"), "PO", 'VD"quoted')
```

- [ ] **Step 2:** run → ImportError for minter.mint.

- [ ] **Step 3: Implement** — `services/token-minter/src/minter/mint.py`:
```python
"""Mint poToken/visitorData from pot-provider, push to Lavalink, persist for cold starts."""

import asyncio
import logging
import os
import re

import aiohttp

log = logging.getLogger("minter")

# Values are injected into a shell-sourced env file and sed'd into YAML at
# lavalink cold start; restrict to base64/URL-safe charset so neither can break.
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9+/=_-]+$")
_RETRY_SECONDS = 300


class MintError(RuntimeError):
    """A mint cycle step failed; the loop retries after a delay."""


async def fetch_tokens(session: aiohttp.ClientSession, provider_url: str) -> tuple[str, str]:
    # Contract pinned by ADR-0004: empty body -> provider generates fresh
    # visitor data and a matching poToken.
    async with session.post(f"{provider_url}/get_pot", json={}) as resp:
        if resp.status != 200:
            raise MintError(f"pot-provider returned HTTP {resp.status}")
        data = await resp.json(content_type=None)
    po_token = data.get("po_token")
    visitor_data = data.get("visitor_data")
    if not po_token or not visitor_data:
        raise MintError(f"pot-provider response missing fields, got keys {sorted(data)}")
    return po_token, visitor_data


async def push_to_lavalink(
    session: aiohttp.ClientSession,
    lavalink_url: str,
    password: str,
    po_token: str,
    visitor_data: str,
) -> None:
    async with session.post(
        f"{lavalink_url}/youtube",
        json={"poToken": po_token, "visitorData": visitor_data},
        headers={"Authorization": password},
    ) as resp:
        if resp.status != 204:
            raise MintError(f"lavalink rejected token push: HTTP {resp.status}")


def write_tokens_file(path: str, po_token: str, visitor_data: str) -> None:
    for name, value in (("po_token", po_token), ("visitor_data", visitor_data)):
        if not _SAFE_VALUE.match(value):
            raise MintError(f"{name} contains characters unsafe for env-file injection")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="ascii") as f:
        f.write(f"POT_TOKEN={po_token}\nPOT_VISITOR_DATA={visitor_data}\n")
    os.replace(tmp, path)


async def run(settings, stop: asyncio.Event) -> None:
    """Mint immediately on start, then every refresh_hours; retry failures after 5 min."""
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while not stop.is_set():
            try:
                po_token, visitor_data = await fetch_tokens(session, settings.pot_provider_url)
                await push_to_lavalink(
                    session,
                    settings.lavalink_url,
                    settings.lavalink_password,
                    po_token,
                    visitor_data,
                )
                write_tokens_file(settings.tokens_file, po_token, visitor_data)
                log.info("minted and pushed fresh poToken (visitorData %.12s...)", visitor_data)
            except (MintError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                log.warning("mint cycle failed: %s — retrying in %ss", exc, _RETRY_SECONDS)
                await _interruptible_sleep(stop, _RETRY_SECONDS)
                continue
            await _interruptible_sleep(stop, settings.refresh_hours * 3600)


async def _interruptible_sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass
```

- [ ] **Step 4:** `python -m pytest tests/ -v` → 10 passed. Then `ruff check .` inside the service → clean.

- [ ] **Step 5:** `git add services/token-minter && git commit -m "feat(minter): mint/push/persist core with contract tests"`

---

### Task 5: minter entrypoint + Dockerfile

**Files:**
- Create: `services/token-minter/src/minter/__main__.py`
- Create: `services/token-minter/Dockerfile`, `services/token-minter/.dockerignore`
- Test: `services/token-minter/tests/test_run_loop.py`

- [ ] **Step 1: Failing test** — `services/token-minter/tests/test_run_loop.py` (verifies the loop mints immediately and stops cleanly):
```python
import asyncio
from dataclasses import dataclass

import pytest
from aiohttp import web

from minter.mint import run
from tests.test_mint import serve  # reuse the fixture  # noqa: F401


@dataclass(frozen=True)
class FakeSettings:
    pot_provider_url: str
    lavalink_url: str
    lavalink_password: str
    tokens_file: str
    refresh_hours: float


async def test_run_mints_immediately_then_waits_and_stops(serve, tmp_path) -> None:  # noqa: F811
    mints = 0

    async def get_pot(request: web.Request) -> web.Response:
        nonlocal mints
        mints += 1
        return web.json_response({"po_token": "PO", "visitor_data": "VD"})

    async def youtube(request: web.Request) -> web.Response:
        return web.Response(status=204)

    provider = await serve([web.post("/get_pot", get_pot)])
    lavalink = await serve([web.post("/youtube", youtube)])
    settings = FakeSettings(provider, lavalink, "pw", str(tmp_path / "tokens.env"), 999.0)

    stop = asyncio.Event()
    task = asyncio.get_running_loop().create_task(run(settings, stop))
    await asyncio.sleep(0.3)
    assert mints == 1  # immediate mint, then sleeping out the 999h interval
    assert (tmp_path / "tokens.env").exists()
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
```

- [ ] **Step 2:** run → fails (run not yet importable / loop wiring absent). If Task 4 already made it pass, verify it fails by checking the file doesn't exist yet — this test exercises `run`, implemented in Task 4; it may pass immediately. If it passes on first run, that's acceptable: note it, don't fake a red.

- [ ] **Step 3:** `services/token-minter/src/minter/__main__.py`:
```python
import asyncio
import logging

from minter import __version__
from minter.config import Settings
from minter.core.runtime import wait_for_shutdown
from minter.mint import run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("minter")


async def main() -> None:
    settings = Settings.from_env()
    log.info("token-minter %s started (refresh every %sh)", __version__, settings.refresh_hours)
    stop = asyncio.Event()
    minter_task = asyncio.get_running_loop().create_task(run(settings, stop))
    await wait_for_shutdown(stop=stop)
    await minter_task
    log.info("shutdown signal received; exiting cleanly")


if __name__ == "__main__":
    asyncio.run(main())
```

`services/token-minter/Dockerfile`:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "minter"]
```
`.dockerignore`: same 4 lines as the other services.

- [ ] **Step 4:** Full suite `python -m pytest tests/ -v` → 11 passed. Build: `docker build -t jacky-minter:dev services/token-minter`. Graceful-stop smoke (no env → should fail fast with KeyError, that's correct crash-only behavior; full-run smoke happens in Task 8's integration step).

- [ ] **Step 5:** Commit + PR-A:
```bash
git add services/token-minter
git commit -m "feat(minter): entrypoint wiring and Dockerfile"
```

---

### Task 6: compose wiring (pot-provider + token-minter)

**Files:**
- Modify: `deploy/docker-compose.yml`
- Modify: `deploy/.env.example`

- [ ] **Step 1:** Add to `deploy/docker-compose.yml` services (keep existing ones untouched; every new service gets the same logging block as the others):
```yaml
  pot-provider:
    image: brainicism/bgutil-ytdlp-pot-provider:node
    restart: unless-stopped
    init: true
    expose:
      - "4416"
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "3"
    networks: [jacky]

  token-minter:
    build: ../services/token-minter
    restart: unless-stopped
    environment:
      POT_PROVIDER_URL: http://pot-provider:4416
      LAVALINK_HOST: lavalink
      LAVALINK_PORT: "2333"
      LAVALINK_PASSWORD: ${LAVALINK_PASSWORD:?set in .env}
      POT_REFRESH_HOURS: ${POT_REFRESH_HOURS:-6}
    volumes:
      - tokens:/data/tokens
    depends_on:
      - lavalink
      - pot-provider
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "3"
    networks: [jacky]
```
Note: `tokens` stays `:ro` on lavalink and is writable (default) on token-minter.

- [ ] **Step 2:** `deploy/.env.example` — append to the YouTube section:
```bash
# How often the token-minter refreshes the poToken (hours). bgutil's own
# cache TTL defaults to 6h; keep these aligned.
POT_REFRESH_HOURS=6
```

- [ ] **Step 3:** Validate: `docker compose -f deploy/docker-compose.yml --env-file deploy/.env config -q && echo OK` → OK.

- [ ] **Step 4:** `git add deploy && git commit -m "feat(deploy): pot-provider sidecar + token-minter wired into the stack"`

---

### Task 7: Lavalink healthcheck + cold-start token injection

**Files:**
- Modify: `services/lavalink/application.yml.tmpl`
- Modify: `services/lavalink/entrypoint.sh`
- Modify: `deploy/docker-compose.yml` (lavalink healthcheck + bot depends_on)

- [ ] **Step 1:** In `application.yml.tmpl`, inside `plugins.youtube` (sibling of `oauth:`), add a marker-fenced pot block, and add the OAuth log level (needed by Task 9):
```yaml
    # POT_BLOCK_START (rendered from /data/tokens/tokens.env at container start)
    pot:
      token: "__POT_TOKEN__"
      visitorData: "__POT_VISITOR_DATA__"
    # POT_BLOCK_END
```
And in the `logging.level` section add:
```yaml
    dev.lavalink.youtube.http.YoutubeOauth2Handler: INFO
```

- [ ] **Step 2:** In `entrypoint.sh`, after the version sed and before the exec, insert:
```sh
TOKENS_FILE="${TOKENS_FILE:-/data/tokens/tokens.env}"
if [ -f "$TOKENS_FILE" ]; then
  . "$TOKENS_FILE"
  sed -i "s|__POT_TOKEN__|${POT_TOKEN}|; s|__POT_VISITOR_DATA__|${POT_VISITOR_DATA}|" /tmp/application.yml
else
  # No minted tokens yet (first boot): drop the pot block entirely.
  sed -i '/# POT_BLOCK_START/,/# POT_BLOCK_END/d' /tmp/application.yml
fi
```
(Values are charset-guarded at write time by the minter, so the sed is safe. Keep LF endings.)

- [ ] **Step 3:** Healthcheck — use the binary ADR-0004 recorded as present in the image (curl or wget). With curl:
```yaml
    healthcheck:
      test: ["CMD", "curl", "-sf", "-H", "Authorization: $${LAVALINK_SERVER_PASSWORD}", "http://localhost:2333/version"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 30s
```
(`$$` escapes compose interpolation so the env var resolves inside the container.) Change the bot service to:
```yaml
    depends_on:
      lavalink:
        condition: service_healthy
```
Guardian's `depends_on` stays plain `- lavalink` — the guardian must be able to start while Lavalink is sick, that's its job.

- [ ] **Step 4: Verify end-to-end locally:**
```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env config -q && echo OK
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build lavalink pot-provider token-minter
sleep 45
docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps   # lavalink healthy?
docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs token-minter | tail -5  # "minted and pushed fresh poToken"?
docker compose -f deploy/docker-compose.yml --env-file deploy/.env restart lavalink && sleep 30
docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec lavalink sh -c 'grep -A2 "pot:" /tmp/application.yml | head -3'  # cold-start injection worked?
docker compose -f deploy/docker-compose.yml --env-file deploy/.env down
```
Expected: lavalink reaches `healthy`; minter logs a successful mint; after restart the rendered config contains the real pot token (not the placeholder).

- [ ] **Step 5:** `git add services/lavalink deploy/docker-compose.yml && git commit -m "feat(lavalink): healthcheck + poToken cold-start injection from tokens volume"` — then PR-B.

---

### Task 8: integration test in CI

**Files:**
- Create: `.github/workflows/integration.yml`

- [ ] **Step 1:** New workflow (separate from ci.yml so unit feedback stays fast):
```yaml
name: Integration

on:
  pull_request:
    paths:
      - "services/**"
      - "deploy/**"
      - ".github/workflows/integration.yml"

concurrency:
  group: integration-${{ github.ref }}
  cancel-in-progress: true

jobs:
  stack-smoke:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - run: cp deploy/.env.example deploy/.env
      - name: Boot audio infrastructure
        run: docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build lavalink pot-provider token-minter
      - name: Wait for lavalink healthy
        run: |
          for i in $(seq 1 20); do
            state=$(docker inspect --format '{{.State.Health.Status}}' $(docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps -q lavalink))
            [ "$state" = "healthy" ] && exit 0
            sleep 5
          done
          docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs lavalink
          exit 1
      - name: Minter completed a cycle
        run: |
          for i in $(seq 1 24); do
            docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs token-minter | grep -q "minted and pushed" && exit 0
            sleep 5
          done
          docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs token-minter pot-provider
          exit 1
      - name: Cold-start injection
        run: |
          docker compose -f deploy/docker-compose.yml --env-file deploy/.env restart lavalink
          sleep 25
          docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec -T lavalink sh -c 'grep -q "__POT_TOKEN__" /tmp/application.yml && exit 1 || grep -q "token:" /tmp/application.yml'
      - if: always()
        run: docker compose -f deploy/docker-compose.yml --env-file deploy/.env down -v
```
Also add `token-minter` to ci.yml's two matrices (`test` and `docker-build`) so unit tests and builds gate PRs like the other services.

- [ ] **Step 2:** Verify the same steps pass locally (they're the Task 7 Step 4 commands), commit:
```bash
git add .github/workflows
git commit -m "ci: integration smoke for the audio infrastructure + minter in unit matrices"
```

---

### Task 9: v2-native reauth flow

**Files:**
- Create: `scripts/reauth-v2.sh` (LF endings — .gitattributes already enforces)
- Modify: `Makefile` (reauth target)

- [ ] **Step 1:** `scripts/reauth-v2.sh`:
```sh
#!/bin/sh
# Playbook F2: interactive YouTube OAuth device flow for the v2 stack.
# Blanks the stored refresh token, recreates lavalink (plugin then starts a
# device flow), surfaces the code, waits for approval, captures the new token
# from the plugin's log line, persists it to deploy/.env, and recreates again.
set -eu
COMPOSE="docker compose -f deploy/docker-compose.yml --env-file deploy/.env"

grep -q '^YOUTUBE_OAUTH_REFRESH_TOKEN=' deploy/.env || {
  echo "deploy/.env missing YOUTUBE_OAUTH_REFRESH_TOKEN line" >&2; exit 1; }

sed -i.bak 's/^YOUTUBE_OAUTH_REFRESH_TOKEN=.*/YOUTUBE_OAUTH_REFRESH_TOKEN=/' deploy/.env
$COMPOSE up -d lavalink

echo "==> Waiting for the device code (up to 60s)..."
code_line=""
i=0
while [ $i -lt 30 ]; do
  code_line=$($COMPOSE logs --tail=300 lavalink 2>/dev/null \
    | grep -iE "activate|device" | grep -iE "code" | tail -1 || true)
  [ -n "$code_line" ] && break
  i=$((i + 1)); sleep 2
done
[ -n "$code_line" ] || { echo "no device code appeared in lavalink logs" >&2; exit 1; }
echo "$code_line"
echo "==> Approve in a browser with the bot's Google account. Waiting for the token (up to 5 min)..."

token=""
i=0
while [ $i -lt 150 ]; do
  token=$($COMPOSE logs --tail=800 lavalink 2>/dev/null \
    | grep -oE '1//[A-Za-z0-9_-]+' | tail -1 || true)
  [ -n "$token" ] && break
  i=$((i + 1)); sleep 2
done
[ -n "$token" ] || { echo "token never appeared — check the YoutubeOauth2Handler log level" >&2; exit 1; }

sed -i.bak "s|^YOUTUBE_OAUTH_REFRESH_TOKEN=.*|YOUTUBE_OAUTH_REFRESH_TOKEN=${token}|" deploy/.env
rm -f deploy/.env.bak
$COMPOSE up -d lavalink
echo "==> New refresh token installed; lavalink recreated. Verify playback or wait for the guardian's next probe."
```
(Google OAuth refresh tokens are prefixed `1//`; the log line is emitted at INFO by `YoutubeOauth2Handler`, which Task 7 enabled in the template.)

- [ ] **Step 2:** Makefile:
```makefile
reauth: ## YouTube OAuth device flow for the v2 stack (playbook F2)
	./scripts/reauth-v2.sh
```

- [ ] **Step 3:** Verify what's verifiable without a Google account: `sh -n scripts/reauth-v2.sh` (syntax), zero CR bytes, and a dry check that the sed lines round-trip on a scratch copy of `.env.example`. Full device-flow verification is a production-cutover (M5) checklist item — note that in the commit message.

- [ ] **Step 4:** `git add scripts/reauth-v2.sh Makefile && git commit -m "feat(ops): v2-native OAuth reauth flow (device-flow verification deferred to M5 checklist)"`

---

### Task 10: docs updates

**Files:**
- Modify: `docs/operations/RUNBOOK.md` (F1, F2), `docs/architecture/ARCHITECTURE.md` (§ token-minter), `README.md` (service table row), `services/lavalink/README.md` (status line), CLAUDE.md if it references M2 pending
- Create: `services/token-minter/README.md`

- [ ] **Step 1:** `services/token-minter/README.md`:
```markdown
# jacky-token-minter

**What:** Keeps the poToken auth layer fresh: asks the pot-provider sidecar
for a new poToken/visitorData every `POT_REFRESH_HOURS`, pushes them to
Lavalink at runtime (`POST /youtube`, no restart), and persists them to the
tokens volume for Lavalink cold starts. Contract with pot-provider is
pinned in ADR-0004.

**Status:** Active from M2.

**Run:** `pip install -e ".[dev]" && python -m minter` · Tests: `pytest`

**Depends on:** pot-provider (POT_PROVIDER_URL), Lavalink REST
(LAVALINK_HOST/PORT/PASSWORD), tokens volume (TOKENS_FILE).
```

- [ ] **Step 2:** Targeted edits, keeping the now-vs-designed discipline:
- RUNBOOK F1: remove the "(effective from M2 ...)" tag; the fix steps become real: `make restart s=token-minter` forces an immediate mint (the loop mints at startup). Update the automated-response wording to reflect that the guardian trigger still lands in M4.
- RUNBOOK F2: replace the M1 caveat blockquote with: `make reauth` now drives the v2 stack (device flow, auto-captures the token). Keep the manual fallback one-liner (copy token into `deploy/.env`, `make up`) for when the log-scrape fails.
- RUNBOOK status banner: drop M2 from the "not yet landed" list (keep M3/M4).
- ARCHITECTURE.md §3.3: update to the pot-provider + minter reality, reference ADR-0004.
- README service table: `services/token-minter` row — remove "(M2)"; add `pot-provider` mention in the row description.
- `services/lavalink/README.md`: status line becomes "poToken volume input live since M2".

- [ ] **Step 3:** Link check over edited files (every `](path)` resolves), then:
```bash
git add docs README.md services/token-minter/README.md services/lavalink/README.md CLAUDE.md
git commit -m "docs: M2 audio infrastructure — runbook F1/F2 now live, ADR-0004 cross-refs"
```
Then PR-C.

---

## Self-review notes

- **Spec coverage (M2 slice):** §3.3 token-minter → Tasks 2–5 (with ADR-0004 superseding the Chromium assumption); §3.2 runtime token push + cold-start persistence → Tasks 4, 7; layered-auth goal §2.1 → poToken layer now real end-to-end; §8 integration-test layer → Task 8; F2 human-flow floor → Task 9; docs discipline §6 → Task 10.
- **Known contract risk, contained:** pot-provider's API is pinned empirically (Task 1) because upstream doesn't document it; the minter's fake-server tests encode the pinned contract so an upstream change fails loudly in CI, not silently in production.
- **Type consistency:** `Settings` field names match between config.py, FakeSettings in tests, and `run()` usage; env var names match compose ↔ .env.example ↔ config.py (`POT_PROVIDER_URL`, `POT_REFRESH_HOURS`, `TOKENS_FILE`, `LAVALINK_*`); tokens.env keys (`POT_TOKEN`, `POT_VISITOR_DATA`) match minter writer ↔ lavalink entrypoint reader.
- **No placeholders** outside ADR-0004's explicitly-required empirical fill-ins and the flagged possibility that fetch_tokens field names shift to match the ADR.
