# Stability Rewrite — M1 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scaffold the new four-service repo structure (bot, guardian, lavalink, deploy), a graceful-shutdown runtime core, templated Lavalink config that makes plugin-version drift impossible, Docker Compose topology, Makefile, CI, and the enterprise docs pack — all without touching the production `bot/` code.

**Architecture:** Monorepo of services under `services/`, deployment under `deploy/`, docs under `docs/`. Old `bot/` + root `docker-compose.yml` keep running production until M5 cutover. Spec: `docs/superpowers/specs/2026-07-02-stability-rewrite-design.md`.

**Tech Stack:** Python 3.11, pytest + pytest-asyncio, ruff, Docker Compose, Lavalink v4 (official image), GitHub Actions.

**PR map (each PR = one GitHub issue, user reviews & merges):**
| PR | Tasks | Branch |
|----|-------|--------|
| PR-1 Service scaffolds + runtime core | 1–4 | `feat/m1-service-scaffolds` |
| PR-2 Lavalink templating + compose + Makefile | 5–7 | `feat/m1-deploy-skeleton` |
| PR-3 CI + GitHub templates | 8–9 | `feat/m1-ci` |
| PR-4 Docs pack + README | 10–11 | `feat/m1-docs` |

---

### Task 1: Bot service scaffold (package + smoke test)

**Files:**
- Create: `services/bot/pyproject.toml`
- Create: `services/bot/src/jacky/__init__.py`
- Create: `services/bot/tests/test_smoke.py`

- [ ] **Step 1: Write the failing test**

`services/bot/tests/test_smoke.py`:
```python
import jacky


def test_package_has_version() -> None:
    assert jacky.__version__ == "2.0.0"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd services/bot && python -m pytest tests/ -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'jacky'` (or collection error; either failure is the point).

- [ ] **Step 3: Create the package**

`services/bot/pyproject.toml`:
```toml
[project]
name = "jacky-bot"
version = "2.0.0"
description = "Jacky Music Discord bot (stability rewrite)"
requires-python = ">=3.11"
dependencies = []

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

`services/bot/src/jacky/__init__.py`:
```python
__version__ = "2.0.0"
```

Note: `dependencies = []` on purpose — discord.py/firebase-admin arrive in M3 when code actually imports them (YAGNI; keeps M1 images small and CI fast).

- [ ] **Step 4: Install and run test to verify it passes**

```bash
cd services/bot && pip install -e ".[dev]" && python -m pytest tests/ -v
```
Expected: `1 passed`

- [ ] **Step 5: Commit**

```bash
git add services/bot
git commit -m "feat(bot): scaffold jacky-bot package with smoke test"
```

---

### Task 2: Bot runtime core (graceful shutdown)

The bot must be a good crash-only citizen: start fast, exit cleanly on SIGTERM (what `docker stop` and the guardian send). This runtime is what M3 builds the real bot on.

**Files:**
- Create: `services/bot/src/jacky/core/__init__.py`
- Create: `services/bot/src/jacky/core/runtime.py`
- Create: `services/bot/src/jacky/__main__.py`
- Test: `services/bot/tests/test_runtime.py`

- [ ] **Step 1: Write the failing test**

`services/bot/tests/test_runtime.py`:
```python
import asyncio

from jacky.core.runtime import wait_for_shutdown


async def test_returns_when_stop_event_set() -> None:
    stop = asyncio.Event()

    async def trigger() -> None:
        await asyncio.sleep(0.01)
        stop.set()

    asyncio.get_running_loop().create_task(trigger())
    await asyncio.wait_for(wait_for_shutdown(stop=stop), timeout=1.0)


async def test_does_not_return_before_signal() -> None:
    stop = asyncio.Event()
    task = asyncio.get_running_loop().create_task(wait_for_shutdown(stop=stop))
    await asyncio.sleep(0.05)
    assert not task.done()
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd services/bot && python -m pytest tests/test_runtime.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'jacky.core'`

- [ ] **Step 3: Implement runtime**

`services/bot/src/jacky/core/__init__.py`: empty file.

`services/bot/src/jacky/core/runtime.py`:
```python
"""Process lifecycle: block until SIGTERM/SIGINT so containers stop cleanly."""

import asyncio
import signal


async def wait_for_shutdown(stop: asyncio.Event | None = None) -> None:
    """Block until SIGTERM/SIGINT (or the injected stop event, for tests)."""
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows dev machines: no loop signal handlers; sync handler suffices.
            signal.signal(sig, lambda *_: stop.set())
    await stop.wait()
```

`services/bot/src/jacky/__main__.py`:
```python
import asyncio
import logging

from jacky import __version__
from jacky.core.runtime import wait_for_shutdown

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("jacky")


async def main() -> None:
    log.info("jacky-bot %s started (M1 scaffold; playback lands in M3)", __version__)
    await wait_for_shutdown()
    log.info("shutdown signal received; exiting cleanly")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run all bot tests, verify pass**

```bash
cd services/bot && python -m pytest tests/ -v
```
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add services/bot
git commit -m "feat(bot): runtime core with graceful SIGTERM shutdown"
```

---

### Task 3: Guardian service scaffold (package + runtime, mirrors Tasks 1–2)

Guardian is a separate installable service. Its runtime is intentionally a copy of the bot's (15 lines; a shared library across two services isn't worth the packaging coupling — recorded in ADR-0003).

**Files:**
- Create: `services/guardian/pyproject.toml`
- Create: `services/guardian/src/guardian/__init__.py`
- Create: `services/guardian/src/guardian/core/__init__.py`
- Create: `services/guardian/src/guardian/core/runtime.py`
- Create: `services/guardian/src/guardian/__main__.py`
- Test: `services/guardian/tests/test_smoke.py`, `services/guardian/tests/test_runtime.py`

- [ ] **Step 1: Write the failing tests**

`services/guardian/tests/test_smoke.py`:
```python
import guardian


def test_package_has_version() -> None:
    assert guardian.__version__ == "2.0.0"
```

`services/guardian/tests/test_runtime.py`:
```python
import asyncio

from guardian.core.runtime import wait_for_shutdown


async def test_returns_when_stop_event_set() -> None:
    stop = asyncio.Event()

    async def trigger() -> None:
        await asyncio.sleep(0.01)
        stop.set()

    asyncio.get_running_loop().create_task(trigger())
    await asyncio.wait_for(wait_for_shutdown(stop=stop), timeout=1.0)
```

- [ ] **Step 2: Run to verify failure**

```bash
cd services/guardian && python -m pytest tests/ -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'guardian'`

- [ ] **Step 3: Implement**

`services/guardian/pyproject.toml` — same as bot's with names swapped:
```toml
[project]
name = "jacky-guardian"
version = "2.0.0"
description = "Supervisor: canary probe, failure classification, recovery, alerting"
requires-python = ">=3.11"
dependencies = []

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

`services/guardian/src/guardian/__init__.py`:
```python
__version__ = "2.0.0"
```

`services/guardian/src/guardian/core/__init__.py`: empty file.

`services/guardian/src/guardian/core/runtime.py`: identical content to `services/bot/src/jacky/core/runtime.py` (see Task 2 Step 3 — copy the whole file).

`services/guardian/src/guardian/__main__.py`:
```python
import asyncio
import logging

from guardian import __version__
from guardian.core.runtime import wait_for_shutdown

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("guardian")


async def main() -> None:
    log.info("guardian %s started (M1 scaffold; probes land in M4)", __version__)
    await wait_for_shutdown()
    log.info("shutdown signal received; exiting cleanly")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run tests, verify pass**

```bash
cd services/guardian && pip install -e ".[dev]" && python -m pytest tests/ -v
```
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add services/guardian
git commit -m "feat(guardian): scaffold jacky-guardian package with runtime core"
```

---

### Task 4: Dockerfiles for bot and guardian

**Files:**
- Create: `services/bot/Dockerfile`
- Create: `services/guardian/Dockerfile`
- Create: `services/bot/.dockerignore`, `services/guardian/.dockerignore`

- [ ] **Step 1: Write both Dockerfiles**

`services/bot/Dockerfile`:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
# Unbuffered logs so `docker logs` is real-time
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "jacky"]
```

`services/guardian/Dockerfile`:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "guardian"]
```

Both `.dockerignore` files (identical content):
```
tests/
__pycache__/
*.egg-info/
.pytest_cache/
```

- [ ] **Step 2: Build both images to verify**

```bash
docker build -t jacky-bot:dev services/bot
docker build -t jacky-guardian:dev services/guardian
```
Expected: both builds succeed.

- [ ] **Step 3: Verify graceful shutdown behavior**

```bash
docker run -d --name bot-smoke jacky-bot:dev && sleep 2 && docker stop bot-smoke && docker logs bot-smoke && docker rm bot-smoke
```
Expected: logs show "started" then "shutdown signal received; exiting cleanly"; `docker stop` returns fast (no 10s SIGKILL timeout).

- [ ] **Step 4: Commit and open PR-1**

```bash
git add services/bot/Dockerfile services/bot/.dockerignore services/guardian/Dockerfile services/guardian/.dockerignore
git commit -m "feat: Dockerfiles for bot and guardian services"
gh pr create --title "M1: service scaffolds + runtime core" --body "Closes #<issue>. Bot + guardian packages, graceful-shutdown runtime (tested), Dockerfiles. Verified: pytest green in both services; docker stop exits cleanly within 2s."
```

---

### Task 5: Lavalink service with templated plugin version

Version drift (playbook F3's worst case) becomes impossible: the youtube-source plugin version exists **only** in `.env`; the config template is rendered at container start.

**Files:**
- Create: `services/lavalink/application.yml.tmpl`
- Create: `services/lavalink/entrypoint.sh`
- Create: `services/lavalink/Dockerfile`

- [ ] **Step 1: Write the config template**

`services/lavalink/application.yml.tmpl` (port/password/OAuth resolve via Spring env placeholders at runtime — secrets never touch disk; the plugin version is sed-rendered because it must be fixed before Lavalink's plugin manager reads it):
```yaml
server:
  port: 2333
  address: 0.0.0.0

lavalink:
  plugins:
    - dependency: "dev.lavalink.youtube:youtube-plugin:__YOUTUBE_PLUGIN_VERSION__"
      repository: "https://maven.lavalink.dev/releases"
      snapshot: false
  server:
    password: "${LAVALINK_SERVER_PASSWORD}"
    sources:
      youtube: false  # handled by youtube-plugin above

plugins:
  youtube:
    enabled: true
    allowSearch: true
    allowDirectVideoIds: true
    allowDirectPlaylistIds: true
    # Client order matters: Lavalink tries top-to-bottom until one resolves.
    # MUSIC: search-only. TVHTML5_SIMPLY first for playback: TV-class IP
    # reputation survives datacenter IPs. TV last: carries OAuth for
    # sign-in-gated streams (metadata support: none, useless alone).
    clients:
      - MUSIC
      - TVHTML5_SIMPLY
      - WEB
      - WEBEMBEDDED
      - ANDROID_VR
      - TV
    clientOptions:
      MUSIC:
        playback: false
        videoLoading: false
    oauth:
      enabled: true
      refreshToken: "${YOUTUBE_OAUTH_REFRESH_TOKEN:}"
      skipInitialization: false

logging:
  level:
    root: INFO
    lavalink: INFO
```

- [ ] **Step 2: Write entrypoint + Dockerfile**

`services/lavalink/entrypoint.sh`:
```sh
#!/bin/sh
set -eu
: "${YOUTUBE_PLUGIN_VERSION:?YOUTUBE_PLUGIN_VERSION must be set (see deploy/.env.example)}"
sed "s|__YOUTUBE_PLUGIN_VERSION__|${YOUTUBE_PLUGIN_VERSION}|g" \
    /opt/Lavalink/application.yml.tmpl > /tmp/application.yml
export SPRING_CONFIG_LOCATION="file:/tmp/application.yml"
exec java -jar /opt/Lavalink/Lavalink.jar
```

`services/lavalink/Dockerfile`:
```dockerfile
FROM ghcr.io/lavalink-devs/lavalink:4
USER root
COPY application.yml.tmpl /opt/Lavalink/application.yml.tmpl
COPY entrypoint.sh /opt/Lavalink/entrypoint.sh
RUN chmod +x /opt/Lavalink/entrypoint.sh
USER lavalink
ENTRYPOINT ["/opt/Lavalink/entrypoint.sh"]
```

- [ ] **Step 3: Build and verify rendering**

```bash
docker build -t jacky-lavalink:dev services/lavalink
docker run --rm -e YOUTUBE_PLUGIN_VERSION=1.18.1 -e LAVALINK_SERVER_PASSWORD=test \
  --entrypoint sh jacky-lavalink:dev \
  -c 'sed "s|__YOUTUBE_PLUGIN_VERSION__|${YOUTUBE_PLUGIN_VERSION}|g" /opt/Lavalink/application.yml.tmpl | grep youtube-plugin'
```
Expected: `- dependency: "dev.lavalink.youtube:youtube-plugin:1.18.1"`

Then a real boot check:
```bash
docker run --rm -d --name ll-smoke -e YOUTUBE_PLUGIN_VERSION=1.18.1 -e LAVALINK_SERVER_PASSWORD=test jacky-lavalink:dev
sleep 25 && docker logs ll-smoke 2>&1 | grep -i -E "started|youtube" ; docker stop ll-smoke
```
Expected: Lavalink "Started Launcher" line and youtube plugin load line, no duplicate-version errors. (If the base image's entrypoint path differs from `/opt/Lavalink/Lavalink.jar`, check `docker inspect ghcr.io/lavalink-devs/lavalink:4 --format '{{.Config.Entrypoint}}'` and match it.)

- [ ] **Step 4: Commit**

```bash
git add services/lavalink
git commit -m "feat(lavalink): templated config — plugin version pinned only in .env"
```

---

### Task 6: Docker Compose topology + .env.example

**Files:**
- Create: `deploy/docker-compose.yml`
- Create: `deploy/.env.example`

- [ ] **Step 1: Write compose file**

`deploy/docker-compose.yml` (token-minter service joins in M2; tokens volume declared now so the contract is visible):
```yaml
name: jacky-music

services:
  lavalink:
    build: ../services/lavalink
    container_name: jacky-lavalink
    restart: unless-stopped
    environment:
      _JAVA_OPTIONS: "-Xmx${LAVALINK_HEAP:-512m}"
      LAVALINK_SERVER_PASSWORD: ${LAVALINK_PASSWORD:?set in .env}
      YOUTUBE_PLUGIN_VERSION: ${YOUTUBE_PLUGIN_VERSION:?set in .env}
      YOUTUBE_OAUTH_REFRESH_TOKEN: ${YOUTUBE_OAUTH_REFRESH_TOKEN:-}
    expose:
      - "2333"
    volumes:
      - tokens:/data/tokens:ro
    networks: [jacky]

  bot:
    build: ../services/bot
    container_name: jacky-bot
    restart: unless-stopped
    environment:
      DISCORD_TOKEN: ${DISCORD_TOKEN:?set in .env}
      LAVALINK_HOST: lavalink
      LAVALINK_PORT: "2333"
      LAVALINK_PASSWORD: ${LAVALINK_PASSWORD:?set in .env}
    depends_on:
      - lavalink
    networks: [jacky]

  guardian:
    build: ../services/guardian
    container_name: jacky-guardian
    restart: unless-stopped
    environment:
      LAVALINK_HOST: lavalink
      LAVALINK_PORT: "2333"
      LAVALINK_PASSWORD: ${LAVALINK_PASSWORD:?set in .env}
      ALERT_WEBHOOK_URL: ${ALERT_WEBHOOK_URL:?set in .env}
    volumes:
      # Guardian's only privilege: restarting sibling containers (playbook F4/F5).
      - /var/run/docker.sock:/var/run/docker.sock
    depends_on:
      - lavalink
    networks: [jacky]

networks:
  jacky: {}

volumes:
  tokens: {}
```

- [ ] **Step 2: Write .env.example**

`deploy/.env.example`:
```bash
# ── Discord ──────────────────────────────────────────────────────────────
# Bot token: Discord Developer Portal → your app → Bot → Token
DISCORD_TOKEN=changeme

# Discord webhook URL for guardian alerts (Server Settings → Integrations →
# Webhooks → New Webhook in your admin channel)
ALERT_WEBHOOK_URL=changeme

# ── Lavalink ─────────────────────────────────────────────────────────────
# Shared secret between bot/guardian and Lavalink. Generate: openssl rand -hex 16
LAVALINK_PASSWORD=changeme

# JVM heap. 512m fits the 2GB VM budget (see spec §2 constraints).
LAVALINK_HEAP=512m

# ── YouTube source ───────────────────────────────────────────────────────
# THE ONLY PLACE the youtube-source plugin version is declared (spec §3.2).
# Latest releases: https://github.com/lavalink-devs/youtube-source/releases
YOUTUBE_PLUGIN_VERSION=1.18.1

# OAuth refresh token (playbook F2). Obtain/rotate with: make reauth
# May be empty; poToken layer (M2) carries playback without it.
YOUTUBE_OAUTH_REFRESH_TOKEN=
```

- [ ] **Step 3: Validate**

```bash
cp deploy/.env.example deploy/.env
docker compose -f deploy/docker-compose.yml --env-file deploy/.env config -q && echo OK
```
Expected: `OK` (no output before it = valid).

- [ ] **Step 4: Verify `deploy/.env` is git-ignored**

```bash
grep -qE "(^|/)\.env$" .gitignore || echo "deploy/.env" >> .gitignore
git check-ignore deploy/.env && echo IGNORED
```
Expected: `IGNORED`

- [ ] **Step 5: Commit**

```bash
git add deploy/docker-compose.yml deploy/.env.example .gitignore
git commit -m "feat(deploy): compose topology for bot/lavalink/guardian + documented env contract"
```

---

### Task 7: Makefile (the single human interface)

**Files:**
- Create: `Makefile`

- [ ] **Step 1: Write it**

`Makefile` (tabs, not spaces, for recipe indentation):
```makefile
COMPOSE := docker compose -f deploy/docker-compose.yml --env-file deploy/.env

.PHONY: help up down restart logs ps test lint build deploy reauth

help: ## List commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-10s %s\n", $$1, $$2}'

up: ## Start the stack
	$(COMPOSE) up -d --build

down: ## Stop the stack
	$(COMPOSE) down

restart: ## Restart one service: make restart s=lavalink
	$(COMPOSE) restart $(s)

logs: ## Tail logs (all, or one: make logs s=bot)
	$(COMPOSE) logs -f --tail=100 $(s)

ps: ## Show service status
	$(COMPOSE) ps

test: ## Run all unit tests
	cd services/bot && python -m pytest tests/ -q
	cd services/guardian && python -m pytest tests/ -q

lint: ## Ruff over both services
	ruff check services/bot services/guardian

build: ## Build all images without starting
	$(COMPOSE) build

deploy: ## Production deploy (run on the VM)
	git pull origin master && $(COMPOSE) up -d --build

reauth: ## Re-run YouTube OAuth device flow (playbook F2)
	./scripts/reauth-youtube.sh
```

- [ ] **Step 2: Verify**

```bash
make help && make lint && make test
```
Expected: command list prints; ruff clean; 5 tests pass. (Windows: run from Git Bash, where `make` is available; the VM and CI are Linux.)

- [ ] **Step 3: Commit and open PR-2**

```bash
git add Makefile
git commit -m "feat: Makefile as the single operational command interface"
gh pr create --title "M1: lavalink templating + compose topology + Makefile" --body "Closes #<issue>. Templated Lavalink config (version drift structurally impossible), compose topology with documented .env contract, Makefile. Verified: compose config valid; lavalink boots with rendered config."
```

---

### Task 8: CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write workflow**

`.github/workflows/ci.yml`:
```yaml
name: CI

on:
  pull_request:
  push:
    branches: [master]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install ruff
      - run: ruff check services/bot services/guardian

  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        service: [bot, guardian]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[dev]"
        working-directory: services/${{ matrix.service }}
      - run: python -m pytest tests/ -v
        working-directory: services/${{ matrix.service }}

  compose-validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cp deploy/.env.example deploy/.env
      - run: docker compose -f deploy/docker-compose.yml --env-file deploy/.env config -q

  docker-build:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        service: [bot, guardian, lavalink]
    steps:
      - uses: actions/checkout@v4
      - run: docker build services/${{ matrix.service }}
```

- [ ] **Step 2: Commit, push, verify CI runs green**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: lint, tests, compose validation, image builds on every PR"
git push -u origin HEAD
gh run watch --exit-status
```
Expected: all four jobs green.

---

### Task 9: Issue & PR templates

**Files:**
- Create: `.github/PULL_REQUEST_TEMPLATE.md`
- Create: `.github/ISSUE_TEMPLATE/feature.md`, `.github/ISSUE_TEMPLATE/bug.md`, `.github/ISSUE_TEMPLATE/ops-incident.md`

- [ ] **Step 1: Write templates**

`.github/PULL_REQUEST_TEMPLATE.md`:
```markdown
## What

<!-- One paragraph: what this PR does -->

## Why

<!-- Link the issue: Closes #N -->

## Test evidence

<!-- Paste test run output or describe manual verification -->

## Checklist

- [ ] CI green
- [ ] Docs updated (service README / ARCHITECTURE.md) if behavior changed
- [ ] RUNBOOK.md updated if failure/recovery behavior changed
- [ ] No secrets in the diff
```

`.github/ISSUE_TEMPLATE/feature.md`:
```markdown
---
name: Feature
about: A unit of planned work
labels: type:feature
---

## Goal

## Acceptance criteria

- [ ]

## Plan reference

<!-- e.g. docs/superpowers/plans/2026-07-02-stability-rewrite-m1-foundation.md Task N -->
```

`.github/ISSUE_TEMPLATE/bug.md`:
```markdown
---
name: Bug
about: Something is broken
labels: type:bug
---

## Observed

## Expected

## Playbook ID (if from a guardian alert)

<!-- F1-F9, see docs/operations/RUNBOOK.md -->

## Logs / evidence
```

`.github/ISSUE_TEMPLATE/ops-incident.md`:
```markdown
---
name: Ops incident
about: Production outage or degradation post-mortem
labels: type:ops
---

## Timeline

## Playbook ID + was detection automatic?

## Root cause

## What would have prevented it

## Follow-up actions

- [ ]
```

- [ ] **Step 2: Commit and open PR-3**

```bash
git add .github
git commit -m "chore: PR template and issue templates (feature/bug/ops-incident)"
gh pr create --title "M1: CI + GitHub templates" --body "Closes #<issue>. CI (lint/test/compose-validate/build) gating every PR, plus PR/issue templates enforcing runbook discipline."
```

---

### Task 10: Docs pack (architecture, runbook, deployment, ADRs, roadmap)

Content sources: the approved spec. Keep each doc self-contained but link to the spec rather than duplicating it wholesale.

**Files:**
- Create: `docs/architecture/ARCHITECTURE.md`
- Create: `docs/architecture/decisions/0001-owned-lavalink-client.md`
- Create: `docs/architecture/decisions/0002-potoken-sidecar.md`
- Create: `docs/architecture/decisions/0003-crash-only-state.md`
- Create: `docs/operations/RUNBOOK.md`
- Create: `docs/operations/DEPLOYMENT.md`
- Create: `docs/roadmap/FUTURE.md`
- Create: `services/bot/README.md`, `services/guardian/README.md`, `services/lavalink/README.md`

- [ ] **Step 1: Write ARCHITECTURE.md**

`docs/architecture/ARCHITECTURE.md`: copy §3 (Architecture, including the diagram and all four component subsections) and §4 (Data Flow) verbatim from `docs/superpowers/specs/2026-07-02-stability-rewrite-design.md`, prefixed by:
```markdown
# Architecture

> Living document. Derived from the approved design spec
> (`docs/superpowers/specs/2026-07-02-stability-rewrite-design.md`);
> update THIS file as the system evolves — the spec stays frozen.

## Governing principle: crash-only

Every container may be killed at any instant; the system converges back to
correct state because durable state lives outside containers (Firestore +
token volume). Recovery is always "restart," never choreography.
```

- [ ] **Step 2: Write the three ADRs**

`docs/architecture/decisions/0001-owned-lavalink-client.md`:
```markdown
# ADR-0001: Own the Lavalink client instead of using wavelink

**Status:** Accepted · 2026-07-02

## Context
wavelink required monkey-patching for Discord DAVE voice encryption, and its
connection-state model obscured silent playback failures (Class B outages).
Recovery behavior — the whole point of this rewrite — lived in a dependency
we didn't control.

## Decision
Write a thin client (~300–400 lines: REST for loads, WebSocket for events)
in `services/bot/src/jacky/audio/`. We own reconnect, backoff, and session
resuming outright.

## Consequences
(+) Full control over failure behavior; no patching. (−) We maintain
protocol code; mitigated by Lavalink v4's stable REST/WS API and tests
against a fake WebSocket.
```

`docs/architecture/decisions/0002-potoken-sidecar.md`:
```markdown
# ADR-0002: poToken sidecar as an independent auth layer

**Status:** Accepted · 2026-07-02

## Context
The dominant outage class (F2): Google periodically revokes the OAuth
refresh token, and recovery needed a human for days. Any single credential
is a single point of failure.

## Decision
Run a scheduled one-shot container (token-minter) that mints a poToken +
visitorData with headless Chromium and pushes them to Lavalink at runtime.
No Google account involved — nothing to revoke. OAuth stays as the second,
independent layer; client ordering as the third.

## Consequences
(+) OAuth revocation no longer stops playback; layers fail independently.
(−) ~400MB RAM spike during each ~1-minute mint (fits budget: one-shot).
```

`docs/architecture/decisions/0003-crash-only-state.md`:
```markdown
# ADR-0003: Crash-only containers; state externalized

**Status:** Accepted · 2026-07-02

## Context
The old bot accreted 1,443 lines of interleaved playback + watchdog code
because in-process recovery required delicate reconnect choreography.

## Decision
No container holds durable state. Firestore is the source of truth (written
BEFORE Lavalink is instructed); tokens live on a named volume. The guardian's
only recovery verb is "restart." Bot and guardian each carry a small
duplicated runtime helper rather than sharing a library — 15 lines of
duplication beats cross-service packaging coupling at this scale.

## Consequences
(+) Recovery logic is trivial and centralized in the guardian; bot code is
pure playback. (−) Firestore write latency is on the command path (~50ms,
acceptable for a music bot).
```

- [ ] **Step 3: Write RUNBOOK.md**

`docs/operations/RUNBOOK.md`: start from spec §5's F1–F9 table (copy it), then one section per ID in this exact format (F2 shown complete; write F1, F3–F9 the same way using the table's detection/response columns):
```markdown
# Operator Runbook

Guardian alerts carry a playbook ID (F1–F9). Find the ID below; run exactly
what it says. All commands run on the VM from the repo root.

<!-- paste spec §5 table here -->

## F2 — YouTube OAuth token revoked

**Alert looks like:** `[F2] OAuth revoked — all loads failing with "requires
login". Run: make reauth`

**Confirm:** `make logs s=lavalink | grep -i oauth` shows
`Invalid status code for oauth2 token fetch: 400` repeating.

**Fix (~60s):**
1. `make reauth` — prints a device code and URL (google.com/device)
2. Open the URL on any machine, enter the code, approve with the bot's Google account
3. The script writes the new token to `deploy/.env` and restarts Lavalink
4. Verify: guardian posts `[F2 resolved]` after its next probe (≤2 min)

**While pending:** poToken layer (F1) keeps most tracks playing; only
sign-in-gated tracks fail.
```

- [ ] **Step 4: Write DEPLOYMENT.md**

`docs/operations/DEPLOYMENT.md`:
```markdown
# Deployment

The deploy contract on ANY Linux host with Docker:
`git clone` → `cp deploy/.env.example deploy/.env` (fill it) → `make up`.
No cloud-specific dependencies.

## Current host: GCP e2-small
- Project `personal-server-492701`, instance `personal-project-machine`
- SSH: `gcloud compute ssh personal-project-machine --project=personal-server-492701`
- Update deploy: `make deploy` (pulls master, rebuilds, restarts changed services)

## Planned host: Hetzner (~€4/mo, better YouTube IP reputation tier)
Migration = the deploy contract above + repoint the external uptime monitor.

## Secrets
Live only in `deploy/.env` on the host (git-ignored). `.env.example`
documents every variable. Rotate `LAVALINK_PASSWORD` freely: `make up`
re-propagates it to all services.

## External uptime monitor (playbook F7)
The VM cannot report its own death. A free-tier monitor (e.g. UptimeRobot)
pings the guardian's heartbeat endpoint (M4) and emails/DMs on silence.
```

- [ ] **Step 5: Write FUTURE.md and service READMEs**

`docs/roadmap/FUTURE.md`: copy spec §10 verbatim under the heading `# Roadmap (deferred — brainstorm before building)`.

`services/bot/README.md`:
```markdown
# jacky-bot

**What:** Discord-facing service — slash commands, voice, playback
orchestration. Zero recovery logic (guardian's job, ADR-0003).

**Run:** `pip install -e ".[dev]" && python -m jacky` · Tests: `pytest`

**Depends on:** Lavalink (REST/WS, env: LAVALINK_HOST/PORT/PASSWORD),
Firestore (source of truth), Discord gateway (DISCORD_TOKEN).
Layout: `commands/` Discord handlers · `audio/` owned Lavalink client +
NodeProvider · `state/` Firestore repositories · `core/` config/lifecycle.
```

`services/guardian/README.md`:
```markdown
# jacky-guardian

**What:** The supervisor. Probes (canary track lookup + bot ping every
2 min), classifies failures to playbook IDs F1–F9, restarts sick containers
(Docker socket), alerts via Discord webhook with the exact fix.

**Run:** `pip install -e ".[dev]" && python -m guardian` · Tests: `pytest`

**Depends on:** Lavalink REST, bot health endpoint, Docker socket
(mounted), ALERT_WEBHOOK_URL. One module per duty: `probe` / `classify` /
`act` / `alert`.
```

`services/lavalink/README.md`:
```markdown
# lavalink

**What:** Lavalink v4 + youtube-source plugin — the audio engine.

**Config:** `application.yml.tmpl` rendered at container start; the plugin
version comes ONLY from `.env` (`YOUTUBE_PLUGIN_VERSION`) so config/jar
drift is impossible. Secrets resolve via Spring env placeholders — never
written to disk.

**Depends on:** YouTube (poToken via token-minter volume + OAuth env +
client ordering — see ADR-0002).
```

- [ ] **Step 6: Commit**

```bash
git add docs services/bot/README.md services/guardian/README.md services/lavalink/README.md
git commit -m "docs: architecture, ADRs 0001-0003, operator runbook F1-F9, deployment, roadmap"
```

---

### Task 11: Root README + CLAUDE.md update

**Files:**
- Modify: `README.md` (replace Architecture section; keep Firebase/frontend setup sections)
- Modify: `CLAUDE.md` (repository structure + commands)

- [ ] **Step 1: Update README architecture section**

Replace the existing `## Architecture` section of `README.md` with:
```markdown
## Architecture (v2 — stability rewrite in progress)

Four crash-only Docker services on one VM; state lives in Firestore + a
token volume, so any container can be restarted at any time.

| Service | Role |
|---------|------|
| `services/bot` | Discord commands, voice, playback (stateless) |
| `services/lavalink` | Audio engine (templated config, layered YouTube auth) |
| `services/token-minter` | Scheduled poToken mint (M2) |
| `services/guardian` | Canary probe → classify (F1–F9) → restart/alert |

Docs: [Architecture](docs/architecture/ARCHITECTURE.md) ·
[Runbook](docs/operations/RUNBOOK.md) ·
[Deployment](docs/operations/DEPLOYMENT.md) ·
[Decisions](docs/architecture/decisions/) ·
[Roadmap](docs/roadmap/FUTURE.md)

Quickstart: `cp deploy/.env.example deploy/.env`, fill it, `make up`.
All commands: `make help`.

> Legacy `bot/` + root `docker-compose.yml` remain in production until the
> M5 cutover; do not add features there.
```

- [ ] **Step 2: Update CLAUDE.md**

In `CLAUDE.md`, replace the `### Repository Structure` and bot/docker command blocks with:
```markdown
### Repository Structure
- `services/bot/` — Python Discord bot v2 (`src/jacky/`) — ACTIVE DEVELOPMENT
- `services/guardian/` — supervisor: probe/classify/act/alert
- `services/lavalink/` — templated Lavalink config
- `deploy/` — docker-compose.yml + .env contract
- `docs/` — architecture, ADRs, runbook, deployment, roadmap
- `bot/` — LEGACY v1 bot (production until M5 cutover; no new features)
- `frontend/`, `functions/` — unchanged (web app + search proxy)

### Commands (v2)
- `make help` — list everything
- `make test` / `make lint` — pytest + ruff over services/
- `make up` / `make logs s=<svc>` / `make restart s=<svc>` — stack ops
- Spec: `docs/superpowers/specs/2026-07-02-stability-rewrite-design.md`
```

- [ ] **Step 3: Commit and open PR-4**

```bash
git add README.md CLAUDE.md
git commit -m "docs: point README and CLAUDE.md at the v2 structure"
gh pr create --title "M1: docs pack + README" --body "Closes #<issue>. ARCHITECTURE.md, ADRs 0001-0003, operator RUNBOOK (F1-F9), DEPLOYMENT.md, roadmap, service READMEs, README/CLAUDE.md updated."
```

---

## Self-review notes

- **Spec coverage (M1 slice):** §3.2 templating → Task 5; §6 structure → Tasks 1–4, 10; §7 workflow → Tasks 8–9 + PR map; §8 unit-test layer → Tasks 1–3 (integration tests arrive with real behavior in M2–M4); §9 deploy contract → Tasks 6–7 + DEPLOYMENT.md. Token-minter (§3.3), owned client (§3.1), guardian duties (§3.4) are M2/M3/M4 plans by design.
- **No placeholders:** every file's full content is inline; RUNBOOK F1/F3–F9 sections follow the fully-written F2 pattern with content from the spec table.
- **Type consistency:** `wait_for_shutdown(stop=...)` signature identical in both services; env var names match across compose/.env.example/Makefile/READMEs (`LAVALINK_PASSWORD`, `YOUTUBE_PLUGIN_VERSION`, `ALERT_WEBHOOK_URL`).
