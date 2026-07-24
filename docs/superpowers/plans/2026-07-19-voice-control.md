# Voice Control ("Hey Jacky") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A companion `voice-listener` service ("Jacky Ears") that hears a per-guild wake phrase in voice channels, recognizes playback commands locally with Vosk, answers with earcon tones, and drives the existing bot's PlayerService — fully removable via one merge revert plus a compose-profile kill-switch.

**Architecture:** New Python service `services/voice-listener/` with its own Discord token joins the session voice channel via `discord-ext-voice-recv`, resamples per-speaker audio to 16 kHz mono, runs a two-state (passive wake-grammar / active command-grammar) Vosk recognizer, and POSTs intents to the main bot. The main bot gains a `/voice-intent` endpoint, session join/leave notifications to the listener, and a `j!wake` command storing the phrase in Firestore. Everything is gated by `VOICE_CONTROL_ENABLED` + compose profile `voice`.

**Tech Stack:** Python 3.11, discord.py ≥2.4, discord-ext-voice-recv, vosk (model `vosk-model-small-en-us-0.15`), aiohttp, pytest. No cloud STT.

**Branch:** all work on `feat/voice-control` (already created; spec committed). Merge to master later with `git merge --no-ff` so `git revert -m 1` removes the feature.

**Spec:** `docs/superpowers/specs/2026-07-18-voice-control-design.md`

---

## File map

```
services/voice-listener/
  pyproject.toml
  Dockerfile
  scripts/gen_earcons.py          # stdlib sine-tone WAV generator (committed output)
  assets/ack.wav confirm.wav error.wav
  src/ears/__init__.py
  src/ears/__main__.py            # entrypoint: config, gateway, api server
  src/ears/config.py              # env settings (fail fast, mirrors bot pattern)
  src/ears/intents.py             # transcript -> Intent (pure)
  src/ears/phrases.py             # wake-phrase normalize/validate, grammar JSON (pure)
  src/ears/engine.py              # per-speaker passive/active Vosk state machine
  src/ears/pipeline.py            # 48k stereo PCM -> 16k mono + RMS silence gate
  src/ears/gateway.py             # Discord client, voice-recv sink, earcons
  src/ears/api.py                 # aiohttp /session /validate /health; intent POSTs out
  tests/test_intents.py test_phrases.py test_engine.py test_pipeline.py test_api.py

services/bot/src/jacky/
  commands/wake.py                # NEW: j!wake command cog
  voice_control.py                # NEW: intent dispatch + listener notifier client
  core/health.py                  # MODIFY: mount POST /voice-intent
  core/runtime.py                 # MODIFY: construct notifier/dispatcher, pass to health
  config.py                       # MODIFY: voice_control_enabled, listener URL, token
  audio/player.py                 # MODIFY: notify listener in begin_session/teardown_session
  tests/test_voice_control.py     # NEW

deploy/docker-compose.yml         # MODIFY: voice-listener service under profiles:["voice"]
deploy/.env.example               # MODIFY: document new vars
Makefile                          # verify test/lint globs pick up services/voice-listener
```

Shared contract (both sides):
- Bot → listener `POST /session` body `{"guild_id": str, "channel_id": str|null, "wake_phrase": str, "action": "join"|"leave"}`, header `X-Voice-Token`.
- Bot → listener `POST /validate` body `{"phrase": str}` → `{"ok": bool, "unknown_words": [str]}`.
- Listener → bot `POST /voice-intent` body `{"guild_id": str, "intent": str, "arg": str|null}`, header `X-Voice-Token`. Intent names: `skip pause resume volume_up volume_down stop play`.

---

### Task 1: Service scaffold + config

**Files:**
- Create: `services/voice-listener/pyproject.toml`, `src/ears/__init__.py`, `src/ears/config.py`, `tests/test_config.py`, `tests/conftest.py` (empty), `.gitignore` (`models/`)

- [ ] **Step 1: Write pyproject** (mirror `services/bot/pyproject.toml` style):

```toml
[project]
name = "jacky-voice-listener"
version = "0.1.0"
description = "Jacky Ears — wake-word voice control companion"
requires-python = ">=3.11"
dependencies = [
    "discord.py[voice]>=2.4",
    "discord-ext-voice-recv>=0.5",
    "vosk>=0.3.45",
    "aiohttp>=3.9",
]

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

- [ ] **Step 2: Write failing test** `tests/test_config.py`:

```python
import pytest

from ears.config import Settings

REQUIRED = {
    "DISCORD_EARS_TOKEN": "tok",
    "VOICE_INTERNAL_TOKEN": "secret",
}

def test_from_env_defaults(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    s = Settings.from_env()
    assert s.bot_intent_url == "http://bot:8080/voice-intent"
    assert s.api_port == 8090
    assert s.model_path == "/models/vosk-small-en"
    assert s.active_window_seconds == 5.0

def test_missing_token_fails_fast(monkeypatch):
    monkeypatch.delenv("DISCORD_EARS_TOKEN", raising=False)
    monkeypatch.setenv("VOICE_INTERNAL_TOKEN", "secret")
    with pytest.raises(KeyError):
        Settings.from_env()
```

- [ ] **Step 3: Run to verify fail:** `cd services/voice-listener && pip install -e .[dev] && pytest tests/test_config.py -v` → FAIL (`ModuleNotFoundError: ears.config`)

- [ ] **Step 4: Implement** `src/ears/config.py` (crash-only, mirrors bot):

```python
"""Environment-driven settings. Fail fast on missing required vars (crash-only)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    discord_token: str
    internal_token: str
    bot_intent_url: str
    api_port: int
    model_path: str
    active_window_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            discord_token=os.environ["DISCORD_EARS_TOKEN"],
            internal_token=os.environ["VOICE_INTERNAL_TOKEN"],
            bot_intent_url=os.environ.get(
                "BOT_INTENT_URL", "http://bot:8080/voice-intent"
            ),
            api_port=int(os.environ.get("EARS_API_PORT", "8090")),
            model_path=os.environ.get("VOSK_MODEL_PATH", "/models/vosk-small-en"),
            active_window_seconds=float(os.environ.get("ACTIVE_WINDOW_SECONDS", "5")),
        )
```

- [ ] **Step 5: Run to verify pass:** `pytest tests/test_config.py -v` → 2 PASS
- [ ] **Step 6: Commit:** `git add services/voice-listener && git commit -m "feat(ears): voice-listener scaffold + settings"`

---

### Task 2: Intent parser (pure)

**Files:** Create `src/ears/intents.py`, `tests/test_intents.py`

- [ ] **Step 1: Failing tests** `tests/test_intents.py`:

```python
import pytest

from ears.intents import Intent, parse_intent

CASES = [
    ("skip", Intent("skip", None)),
    ("next", Intent("skip", None)),
    ("skip this track", Intent("skip", None)),
    ("pause", Intent("pause", None)),
    ("resume", Intent("resume", None)),
    ("play", Intent("resume", None)),          # bare "play" = resume
    ("stop", Intent("stop", None)),
    ("volume up", Intent("volume_up", None)),
    ("louder", Intent("volume_up", None)),
    ("volume down", Intent("volume_down", None)),
    ("quieter", Intent("volume_down", None)),
    ("play never gonna give you up", Intent("play", "never gonna give you up")),
]

@pytest.mark.parametrize("text,expected", CASES)
def test_parse(text, expected):
    assert parse_intent(text) == expected

def test_unrecognized_returns_none():
    assert parse_intent("open the pod bay doors") is None
    assert parse_intent("") is None
```

- [ ] **Step 2: Verify fail:** `pytest tests/test_intents.py -v` → FAIL
- [ ] **Step 3: Implement** `src/ears/intents.py`:

```python
"""Transcript -> Intent. Pure functions; grammar lives in COMMAND_WORDS."""

from dataclasses import dataclass

# Words the ACTIVE-mode Vosk grammar is allowed to hear (plus free dictation
# via "[unk]" for the play-title tail — see engine.build_active_grammar).
COMMAND_WORDS = [
    "skip", "next", "pause", "resume", "play", "stop",
    "volume", "up", "down", "louder", "quieter", "track", "this", "the", "song",
]

_EXACT = {
    "pause": "pause",
    "resume": "resume",
    "stop": "stop",
    "louder": "volume_up",
    "quieter": "volume_down",
}


@dataclass(frozen=True)
class Intent:
    name: str
    arg: str | None


def parse_intent(text: str) -> Intent | None:
    words = text.lower().split()
    if not words:
        return None
    head = words[0]
    if head in ("skip", "next"):
        return Intent("skip", None)
    if head in _EXACT:
        return Intent(_EXACT[head], None)
    if head == "volume" and len(words) >= 2 and words[1] in ("up", "down"):
        return Intent(f"volume_{words[1]}", None)
    if head == "play":
        tail = " ".join(words[1:]).strip()
        return Intent("play", tail) if tail else Intent("resume", None)
    return None
```

- [ ] **Step 4: Verify pass:** `pytest tests/test_intents.py -v` → all PASS
- [ ] **Step 5: Commit:** `git commit -am "feat(ears): transcript-to-intent parser"`

---

### Task 3: Wake-phrase rules + grammars (pure)

**Files:** Create `src/ears/phrases.py`, `tests/test_phrases.py`

- [ ] **Step 1: Failing tests** `tests/test_phrases.py`:

```python
from ears.intents import COMMAND_WORDS
from ears.phrases import (
    build_active_grammar,
    build_passive_grammar,
    normalize_phrase,
    validate_phrase,
)

VOCAB = {"hey", "jacky", "okay", "dj", "music"}          # stand-in model vocab
KNOWS = VOCAB.__contains__

def test_normalize():
    assert normalize_phrase("  Hey, JACKY! ") == "hey jacky"

def test_validate_ok():
    assert validate_phrase("hey jacky", KNOWS) == []

def test_validate_word_count():
    assert validate_phrase("jacky", KNOWS) == ["need 2-4 words"]
    assert validate_phrase("a b c d e", KNOWS) == ["need 2-4 words"]

def test_validate_unknown_words():
    assert validate_phrase("hey zorblatt", KNOWS) == ["unknown word: zorblatt"]

def test_passive_grammar_is_phrase_plus_unk():
    import json
    assert json.loads(build_passive_grammar("hey jacky")) == ["hey jacky", "[unk]"]

def test_active_grammar_includes_commands_and_unk():
    import json
    g = json.loads(build_active_grammar())
    assert "[unk]" in g
    for w in COMMAND_WORDS:
        assert w in g
```

- [ ] **Step 2: Verify fail:** `pytest tests/test_phrases.py -v` → FAIL
- [ ] **Step 3: Implement** `src/ears/phrases.py`:

```python
"""Wake-phrase normalization/validation and Vosk grammar builders.

Vosk grammars are JSON lists of allowed utterances; "[unk]" absorbs everything
else so random speech doesn't get force-matched onto the wake phrase.
"""

import json
import re
from collections.abc import Callable

from ears.intents import COMMAND_WORDS

_WORD = re.compile(r"[a-z']+")


def normalize_phrase(raw: str) -> str:
    return " ".join(_WORD.findall(raw.lower()))


def validate_phrase(raw: str, knows_word: Callable[[str], bool]) -> list[str]:
    """Return a list of problems; empty list means valid (2-4 known words)."""
    words = normalize_phrase(raw).split()
    if not 2 <= len(words) <= 4:
        return ["need 2-4 words"]
    return [f"unknown word: {w}" for w in words if not knows_word(w)]


def build_passive_grammar(phrase: str) -> str:
    return json.dumps([normalize_phrase(phrase), "[unk]"])


def build_active_grammar() -> str:
    return json.dumps([*COMMAND_WORDS, "[unk]"])
```

- [ ] **Step 4: Verify pass**, then **Commit:** `git commit -am "feat(ears): wake-phrase validation + grammar builders"`

---

### Task 4: Recognizer state machine

**Files:** Create `src/ears/engine.py`, `tests/test_engine.py`

The engine wraps Vosk behind a `RecognizerFactory` protocol so tests inject fakes (no model download in CI). One `SpeakerEngine` per (guild, user); the Vosk `Model` is shared and injected once at startup.

- [ ] **Step 1: Failing tests** `tests/test_engine.py`:

```python
from ears.engine import SpeakerEngine
from ears.intents import Intent


class FakeRec:
    """Scripted recognizer: yields queued finals, one per feed() call."""
    def __init__(self):
        self.finals: list[str] = []
        self.grammar_resets = 0
    def accept(self, pcm: bytes) -> str | None:
        return self.finals.pop(0) if self.finals else None
    def reset(self) -> None:
        self.grammar_resets += 1


def make_engine(**kw):
    passive, active = FakeRec(), FakeRec()
    eng = SpeakerEngine(
        passive=passive, active=active, wake_phrase="hey jacky",
        active_window_seconds=5.0, clock=lambda: kw.get("now", [0.0])[0],
    )
    return eng, passive, active, kw.get("now", [0.0])


def test_wake_then_command():
    eng, passive, active, now = make_engine(now=[0.0])
    passive.finals = ["hey jacky"]
    assert eng.feed(b"..") == ("wake", None)          # ack tone cue
    active.finals = ["skip"]
    assert eng.feed(b"..") == ("intent", Intent("skip", None))
    assert eng.state == "passive"                     # returns after a command


def test_ignores_non_wake_speech():
    eng, passive, _, _ = make_engine()
    passive.finals = ["[unk] something else"]
    assert eng.feed(b"..") is None
    assert eng.state == "passive"


def test_active_window_times_out():
    now = [0.0]
    eng, passive, active, _ = make_engine(now=now)
    passive.finals = ["hey jacky"]
    eng.feed(b"..")
    now[0] = 6.0                                       # past 5s window
    assert eng.feed(b"..") == ("timeout", None)
    assert eng.state == "passive"


def test_garbage_in_active_window_is_error():
    eng, passive, active, _ = make_engine()
    passive.finals = ["hey jacky"]
    eng.feed(b"..")
    active.finals = ["[unk] [unk]"]
    assert eng.feed(b"..") == ("error", None)          # buzz cue
    assert eng.state == "passive"
```

- [ ] **Step 2: Verify fail**, **Step 3: Implement** `src/ears/engine.py`:

```python
"""Per-speaker passive/active recognition state machine.

Events returned by feed():
  ("wake", None)      wake phrase heard -> caller plays ack tone
  ("intent", Intent)  command recognized -> caller ships it + confirm tone
  ("error", None)     active-window speech not understood -> error buzz
  ("timeout", None)   active window expired silently -> passive
  None                nothing notable
"""

import json
import time
from typing import Callable, Protocol

from ears.intents import Intent, parse_intent
from ears.phrases import normalize_phrase


class Recognizer(Protocol):
    def accept(self, pcm: bytes) -> str | None: ...
    def reset(self) -> None: ...


class VoskRecognizer:
    """Thin adapter over vosk.KaldiRecognizer (16 kHz, grammar-constrained)."""

    def __init__(self, model, grammar_json: str, sample_rate: int = 16000):
        from vosk import KaldiRecognizer
        self._rec = KaldiRecognizer(model, sample_rate, grammar_json)

    def accept(self, pcm: bytes) -> str | None:
        if self._rec.AcceptWaveform(pcm):
            return json.loads(self._rec.Result()).get("text") or None
        return None

    def reset(self) -> None:
        self._rec.Reset()


class SpeakerEngine:
    def __init__(self, passive: Recognizer, active: Recognizer, wake_phrase: str,
                 active_window_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.passive, self.active = passive, active
        self.wake_phrase = normalize_phrase(wake_phrase)
        self.window = active_window_seconds
        self.clock = clock
        self.state = "passive"
        self._active_until = 0.0

    def feed(self, pcm: bytes):
        if self.state == "passive":
            text = self.passive.accept(pcm)
            if text and self.wake_phrase in text:
                self.state = "active"
                self._active_until = self.clock() + self.window
                self.active.reset()
                return ("wake", None)
            return None
        if self.clock() > self._active_until:
            self.state = "passive"
            return ("timeout", None)
        text = self.active.accept(pcm)
        if text is None:
            return None
        self.state = "passive"
        intent = parse_intent(text.replace("[unk]", " ").strip())
        return ("intent", intent) if intent else ("error", None)
```

- [ ] **Step 4: Verify pass:** `pytest tests/test_engine.py -v`, then **Commit:** `git commit -am "feat(ears): passive/active speaker engine"`

---

### Task 5: Audio pipeline (resample + silence gate)

**Files:** Create `src/ears/pipeline.py`, `tests/test_pipeline.py`

- [ ] **Step 1: Failing tests** `tests/test_pipeline.py`:

```python
import math
import struct

from ears.pipeline import Downsampler, is_silence


def sine_48k_stereo(ms: int, freq: int = 440, amp: int = 12000) -> bytes:
    frames = 48 * ms
    out = bytearray()
    for i in range(frames):
        v = int(amp * math.sin(2 * math.pi * freq * i / 48000))
        out += struct.pack("<hh", v, v)
    return bytes(out)


def test_downsample_ratio():
    ds = Downsampler()
    out = ds.feed(sine_48k_stereo(20))            # one Discord frame
    # 48k stereo 16-bit -> 16k mono 16-bit: byte count shrinks 6x
    assert len(out) == len(sine_48k_stereo(20)) // 6


def test_silence_gate():
    assert is_silence(b"\x00\x00" * 960)
    assert not is_silence(sine_48k_stereo(20))
```

- [ ] **Step 2: Verify fail**, **Step 3: Implement** `src/ears/pipeline.py`:

```python
"""48 kHz stereo s16le (Discord) -> 16 kHz mono s16le (Vosk), plus RMS gate.

audioop is deprecated for 3.13 but we pin python 3.11 in the image; swap to
`audioop-lts` if the base image ever moves.
"""

import audioop

SILENCE_RMS = 200          # empirically well below quiet speech


def is_silence(pcm_48k_stereo: bytes) -> bool:
    return audioop.rms(pcm_48k_stereo, 2) < SILENCE_RMS


class Downsampler:
    """Stateful (ratecv carries filter state between frames); one per speaker."""

    def __init__(self):
        self._state = None

    def feed(self, pcm_48k_stereo: bytes) -> bytes:
        mono = audioop.tomono(pcm_48k_stereo, 2, 0.5, 0.5)
        out, self._state = audioop.ratecv(mono, 2, 1, 48000, 16000, self._state)
        return out
```

- [ ] **Step 4: Verify pass**, **Commit:** `git commit -am "feat(ears): audio downsample + silence gate"`

---

### Task 6: Earcon assets

**Files:** Create `scripts/gen_earcons.py`, run it, commit `assets/*.wav`

- [ ] **Step 1: Write generator** `scripts/gen_earcons.py` (stdlib only):

```python
"""Generate earcon WAVs (48k stereo s16le, FFmpeg-friendly). Run from
services/voice-listener: python scripts/gen_earcons.py"""

import math
import struct
import wave
from pathlib import Path

RATE = 48000

def tone(freqs: list[tuple[float, float]], amp: float = 0.35) -> bytes:
    out = bytearray()
    for freq, dur in freqs:
        n = int(RATE * dur)
        for i in range(n):
            env = min(1.0, i / 480, (n - i) / 480)          # 10ms fade in/out
            v = int(32767 * amp * env * math.sin(2 * math.pi * freq * i / RATE))
            out += struct.pack("<hh", v, v)
    return bytes(out)

EARCONS = {
    "ack.wav": [(660, 0.09), (880, 0.12)],       # rising: "listening"
    "confirm.wav": [(880, 0.08)],                # blip: "done"
    "error.wav": [(220, 0.18)],                  # low buzz: "didn't get that"
}

assets = Path(__file__).resolve().parent.parent / "assets"
assets.mkdir(exist_ok=True)
for name, spec in EARCONS.items():
    with wave.open(str(assets / name), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(tone(spec))
    print("wrote", assets / name)
```

- [ ] **Step 2: Run:** `python scripts/gen_earcons.py` → three files written
- [ ] **Step 3: Commit:** `git add scripts assets && git commit -m "feat(ears): earcon tones (generated, script included)"`

---### Task 7: Listener API + intent shipping

**Files:** Create `src/ears/api.py`, `tests/test_api.py`

`api.py` owns both directions: the inbound aiohttp app (`/session`, `/validate`, `/health`) and the outbound `ship_intent` POST. `gateway` is passed in behind a small protocol so tests use a stub.

- [ ] **Step 1: Failing tests** `tests/test_api.py`:

```python
import pytest
from aiohttp.test_utils import TestClient, TestServer

from ears.api import build_app


class StubGateway:
    def __init__(self):
        self.calls = []
        self.vocab = {"hey", "jacky"}
    async def join(self, guild_id: str, channel_id: str, wake_phrase: str):
        self.calls.append(("join", guild_id, channel_id, wake_phrase))
    async def leave(self, guild_id: str):
        self.calls.append(("leave", guild_id))
    def knows_word(self, w: str) -> bool:
        return w in self.vocab


@pytest.fixture
async def client():
    gw = StubGateway()
    app = build_app(gw, internal_token="sekrit")
    c = TestClient(TestServer(app))
    await c.start_server()
    yield c, gw
    await c.close()


async def test_session_join(client):
    c, gw = client
    r = await c.post("/session", json={
        "guild_id": "1", "channel_id": "2",
        "wake_phrase": "hey jacky", "action": "join",
    }, headers={"X-Voice-Token": "sekrit"})
    assert r.status == 200
    assert gw.calls == [("join", "1", "2", "hey jacky")]


async def test_bad_token_rejected(client):
    c, _ = client
    r = await c.post("/session", json={}, headers={"X-Voice-Token": "wrong"})
    assert r.status == 401


async def test_validate(client):
    c, _ = client
    r = await c.post("/validate", json={"phrase": "hey zorblatt"},
                     headers={"X-Voice-Token": "sekrit"})
    body = await r.json()
    assert body == {"ok": False, "problems": ["unknown word: zorblatt"]}


async def test_health_open(client):
    c, _ = client
    r = await c.get("/health")
    assert r.status == 200
```

- [ ] **Step 2: Verify fail**, **Step 3: Implement** `src/ears/api.py`:

```python
"""Inbound control API (bot -> listener) and outbound intent shipping."""

import logging
from typing import Any, Protocol

import aiohttp
from aiohttp import web

from ears.phrases import validate_phrase

log = logging.getLogger("ears.api")


class Gateway(Protocol):
    async def join(self, guild_id: str, channel_id: str, wake_phrase: str) -> None: ...
    async def leave(self, guild_id: str) -> None: ...
    def knows_word(self, word: str) -> bool: ...


def build_app(gateway: Gateway, internal_token: str) -> web.Application:
    @web.middleware
    async def auth(request: web.Request, handler):
        if request.path != "/health" and \
                request.headers.get("X-Voice-Token") != internal_token:
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    async def session(request: web.Request) -> web.Response:
        body = await request.json()
        if body.get("action") == "leave":
            await gateway.leave(body["guild_id"])
        else:
            await gateway.join(body["guild_id"], body["channel_id"],
                               body.get("wake_phrase") or "hey jacky")
        return web.json_response({"ok": True})

    async def validate(request: web.Request) -> web.Response:
        body = await request.json()
        problems = validate_phrase(body.get("phrase", ""), gateway.knows_word)
        return web.json_response({"ok": not problems, "problems": problems})

    async def health(_r: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app = web.Application(middlewares=[auth])
    app.add_routes([web.post("/session", session), web.post("/validate", validate),
                    web.get("/health", health)])
    return app


async def ship_intent(session: aiohttp.ClientSession, url: str, token: str,
                      guild_id: str, intent: Any) -> bool:
    """POST a recognized intent to the bot. Returns False on any failure
    (caller plays the error buzz; intents are fire-and-forget, never queued)."""
    try:
        async with session.post(url, json={
            "guild_id": guild_id, "intent": intent.name, "arg": intent.arg,
        }, headers={"X-Voice-Token": token},
                timeout=aiohttp.ClientTimeout(total=5)) as resp:
            return resp.status == 200
    except aiohttp.ClientError as exc:
        log.warning("intent ship failed: %s", exc)
        return False
```

- [ ] **Step 4: Verify pass:** `pytest tests/test_api.py -v`, **Commit:** `git commit -am "feat(ears): control API + intent shipping"`

---

### Task 8: Discord gateway (voice receive + earcons)

**Files:** Create `src/ears/gateway.py`, `src/ears/__main__.py`

Thin Discord I/O layer — everything testable lives in Tasks 2–5, so this task has no unit tests beyond an import smoke; it is exercised in the soak test (Task 11).

- [ ] **Step 1: Implement** `src/ears/gateway.py`:

```python
"""Jacky Ears Discord client: join/leave voice, receive audio, play earcons.

Audio path (voice_recv callback thread -> asyncio):
  AudioSink.write(user, data) -> Downsampler -> silence gate -> SpeakerEngine
  engine events -> loop.call_soon_threadsafe -> earcon + ship_intent
"""

import asyncio
import logging
from pathlib import Path

import discord
from discord.ext import voice_recv

from ears.api import ship_intent
from ears.config import Settings
from ears.engine import SpeakerEngine, VoskRecognizer
from ears.phrases import build_active_grammar, build_passive_grammar
from ears.pipeline import Downsampler, is_silence

log = logging.getLogger("ears.gateway")
ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"


class EarsSink(voice_recv.AudioSink):
    """Fan out per-speaker PCM into engines. Runs on voice-recv's thread."""

    def __init__(self, client: "EarsClient", guild_id: str, wake_phrase: str):
        super().__init__()
        self.client, self.guild_id, self.wake_phrase = client, guild_id, wake_phrase
        self.engines: dict[int, tuple[Downsampler, SpeakerEngine]] = {}

    def wants_opus(self) -> bool:
        return False                      # receive decoded 48k stereo PCM

    def write(self, user, data: voice_recv.VoiceData) -> None:
        if user is None or user.bot:
            return
        if is_silence(data.pcm):
            return
        # NB: not setdefault(user.id, self._new_engine()) — that eagerly builds
        # (and discards) two Vosk recognizers on EVERY frame (~50/s/speaker).
        pair = self.engines.get(user.id)
        if pair is None:
            pair = self.engines[user.id] = self._new_engine()
        ds, eng = pair
        event = eng.feed(ds.feed(data.pcm))
        if event:
            self.client.dispatch_event(self.guild_id, event)

    def _new_engine(self) -> tuple[Downsampler, SpeakerEngine]:
        model = self.client.model
        return Downsampler(), SpeakerEngine(
            passive=VoskRecognizer(model, build_passive_grammar(self.wake_phrase)),
            active=VoskRecognizer(model, build_active_grammar()),
            wake_phrase=self.wake_phrase,
            active_window_seconds=self.client.settings.active_window_seconds,
        )

    def cleanup(self) -> None:
        self.engines.clear()


class EarsClient(discord.Client):
    def __init__(self, settings: Settings):
        super().__init__(intents=discord.Intents(guilds=True, voice_states=True))
        self.settings = settings
        self.model = None                 # vosk.Model, loaded in setup_hook
        self.http_session = None          # aiohttp.ClientSession
        self._vocab: set[str] = set()

    async def setup_hook(self) -> None:
        import aiohttp
        from vosk import Model
        self.model = await asyncio.to_thread(Model, self.settings.model_path)
        words = Path(self.settings.model_path, "graph", "words.txt")
        if words.exists():
            self._vocab = {ln.split()[0] for ln in words.read_text().splitlines() if ln}
        self.http_session = aiohttp.ClientSession()

    def knows_word(self, word: str) -> bool:
        return not self._vocab or word in self._vocab

    async def join(self, guild_id: str, channel_id: str, wake_phrase: str) -> None:
        await self.leave(guild_id)
        channel = self.get_channel(int(channel_id))
        if channel is None:
            log.warning("join ignored: channel %s not visible", channel_id)
            return
        vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        vc.listen(EarsSink(self, guild_id, wake_phrase))
        log.info("listening in guild %s channel %s (wake=%r)",
                 guild_id, channel_id, wake_phrase)

    async def leave(self, guild_id: str) -> None:
        guild = self.get_guild(int(guild_id))
        if guild and guild.voice_client:
            await guild.voice_client.disconnect(force=True)

    # -- engine events (called from sink thread) ------------------------------
    def dispatch_event(self, guild_id: str, event) -> None:
        self.loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._handle_event(guild_id, event))
        )

    async def _handle_event(self, guild_id: str, event) -> None:
        kind, intent = event
        if kind == "wake":
            self._play_earcon(guild_id, "ack.wav")
        elif kind == "error":
            self._play_earcon(guild_id, "error.wav")
        elif kind == "intent":
            ok = await ship_intent(self.http_session, self.settings.bot_intent_url,
                                   self.settings.internal_token, guild_id, intent)
            self._play_earcon(guild_id, "confirm.wav" if ok else "error.wav")

    def _play_earcon(self, guild_id: str, name: str) -> None:
        guild = self.get_guild(int(guild_id))
        vc = guild.voice_client if guild else None
        if vc and not vc.is_playing():
            vc.play(discord.FFmpegPCMAudio(str(ASSETS / name)))
```

- [ ] **Step 2: Implement** `src/ears/__main__.py`:

```python
import asyncio
import logging

from aiohttp import web

from ears.api import build_app
from ears.config import Settings
from ears.gateway import EarsClient

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ears")


async def main() -> None:
    settings = Settings.from_env()
    client = EarsClient(settings)
    runner = web.AppRunner(build_app(client, settings.internal_token))

    async with client:
        await client.login(settings.discord_token)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", settings.api_port).start()
        log.info("ears 0.1.0 started (api :%d)", settings.api_port)
        await client.connect()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: Smoke test imports** (no Discord/model needed): `python -c "import ears.gateway, ears.__main__"` inside the container build (next task) — locally just run `ruff check .` and `pytest -q` → all green.
- [ ] **Step 4: Commit:** `git commit -am "feat(ears): discord gateway, voice receive, earcon playback"`

---

### Task 9: Bot-side integration

**Files:**
- Create: `services/bot/src/jacky/voice_control.py`, `services/bot/src/jacky/commands/wake.py`, `services/bot/tests/test_voice_control.py`
- Modify: `services/bot/src/jacky/config.py`, `core/health.py`, `core/runtime.py`, `audio/player.py`

- [ ] **Step 1: Extend Settings** (`config.py`) — add fields (all optional; feature dormant without them):

```python
    # inside @dataclass Settings — voice control (dormant unless enabled)
    voice_control_enabled: bool
    voice_listener_url: str
    voice_internal_token: str
```
and in `from_env()`:
```python
            voice_control_enabled=os.environ.get("VOICE_CONTROL_ENABLED", "")
                .lower() in ("1", "true", "yes"),
            voice_listener_url=os.environ.get(
                "VOICE_LISTENER_URL", "http://voice-listener:8090"
            ),
            voice_internal_token=os.environ.get("VOICE_INTERNAL_TOKEN", ""),
```

- [ ] **Step 2: Failing tests** `tests/test_voice_control.py` (follow `tests/test_player.py` fixture style — `AsyncMock` service/repo):

```python
from unittest.mock import AsyncMock

import pytest

from jacky.voice_control import VoiceIntentDispatcher


@pytest.fixture
def service():
    s = AsyncMock()
    s.set_volume.side_effect = lambda gid, v: max(0, min(100, v))
    return s


@pytest.fixture
def repo():
    r = AsyncMock()
    r.get_state.return_value = {"volume": 80, "currentTrack": None}
    return r


@pytest.fixture
def dispatcher(service, repo):
    return VoiceIntentDispatcher(service, repo)


async def test_skip(dispatcher, service):
    assert await dispatcher.dispatch(1, "skip", None)
    service.skip.assert_awaited_once_with(1)


async def test_pause_resume(dispatcher, service):
    await dispatcher.dispatch(1, "pause", None)
    service.pause.assert_awaited_with(1, True)
    await dispatcher.dispatch(1, "resume", None)
    service.pause.assert_awaited_with(1, False)


async def test_volume_steps_from_state(dispatcher, service):
    await dispatcher.dispatch(1, "volume_up", None)
    service.set_volume.assert_awaited_with(1, 90)      # 80 + 10
    await dispatcher.dispatch(1, "volume_down", None)
    service.set_volume.assert_awaited_with(1, 70)


async def test_play_starts_when_idle(dispatcher, service):
    track = object()
    service.resolve.return_value = AsyncMock(
        tracks=[track], first=track, kind="track", playlist_name=None
    )
    assert await dispatcher.dispatch(1, "play", "test song")
    service.start_current_track.assert_awaited_once()


async def test_unknown_intent_rejected(dispatcher):
    assert not await dispatcher.dispatch(1, "reboot", None)
```

- [ ] **Step 3: Verify fail:** `cd services/bot && pytest tests/test_voice_control.py -v` → FAIL
- [ ] **Step 4: Implement** `src/jacky/voice_control.py`:

```python
"""Voice-control glue: dispatch listener intents onto PlayerService, and
notify the listener when sessions start/stop. Whole module is dormant when
settings.voice_control_enabled is False (nothing constructs it)."""

import logging
from typing import Any

import aiohttp

from jacky.audio.models import to_track_data

log = logging.getLogger("jacky.voice")

VOLUME_STEP = 10


class VoiceIntentDispatcher:
    def __init__(self, service: Any, repo: Any):
        self.service, self.repo = service, repo

    async def dispatch(self, guild_id: int, intent: str, arg: str | None) -> bool:
        sid = str(guild_id)
        if intent == "skip":
            await self.service.skip(guild_id)
        elif intent == "pause":
            await self.service.pause(guild_id, True)
        elif intent == "resume":
            await self.service.pause(guild_id, False)
        elif intent == "stop":
            await self.service.teardown_session(guild_id, clear_queue=True)
        elif intent in ("volume_up", "volume_down"):
            state = await self.repo.get_state(sid) or {}
            current = int(state.get("volume", 80))
            delta = VOLUME_STEP if intent == "volume_up" else -VOLUME_STEP
            await self.service.set_volume(guild_id, current + delta)
        elif intent == "play" and arg:
            return await self._play(guild_id, sid, arg)
        else:
            return False
        return True

    async def _play(self, guild_id: int, sid: str, query: str) -> bool:
        """Mirrors commands/playback.play, minus Discord I/O (voice-requested
        tracks are announced by the usual now-playing flow)."""
        result = await self.service.resolve(query)
        if not result.tracks:
            return False
        td = to_track_data(result.first, "voice command")
        state = await self.repo.get_state(sid) or {}
        if state.get("currentTrack"):
            await self.repo.add_to_queue(sid, td)
            return True
        return bool(await self.service.start_current_track(guild_id, result.first, td))


class ListenerNotifier:
    """Bot -> voice-listener control calls. All failures are soft: voice
    control degrades, music never does."""

    def __init__(self, base_url: str, token: str, repo: Any):
        self.base_url, self.token, self.repo = base_url.rstrip("/"), token, repo

    async def _post(self, path: str, body: dict) -> dict | None:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(f"{self.base_url}{path}", json=body,
                                  headers={"X-Voice-Token": self.token},
                                  timeout=aiohttp.ClientTimeout(total=5)) as r:
                    return await r.json() if r.status == 200 else None
        except aiohttp.ClientError as exc:
            log.warning("listener call %s failed: %s", path, exc)
            return None

    async def session_started(self, guild_id: int, channel_id: str) -> None:
        state = await self.repo.get_state(str(guild_id)) or {}
        await self._post("/session", {
            "guild_id": str(guild_id), "channel_id": channel_id,
            "wake_phrase": state.get("wakePhrase") or "hey jacky",
            "action": "join",
        })

    async def session_ended(self, guild_id: int) -> None:
        await self._post("/session", {
            "guild_id": str(guild_id), "channel_id": None,
            "wake_phrase": "", "action": "leave",
        })

    async def validate_phrase(self, phrase: str) -> dict | None:
        return await self._post("/validate", {"phrase": phrase})
```

- [ ] **Step 5: Verify pass:** `pytest tests/test_voice_control.py -v` → all PASS, **Commit:** `git commit -am "feat(bot): voice intent dispatcher + listener notifier"`

- [ ] **Step 6: Mount `/voice-intent`** — in `core/health.py` `build_app`, accept two new optional params and add the route:

```python
def build_app(bot: Any, service: Any, dispatcher: Any = None,
              voice_token: str = "") -> web.Application:
    ...existing health handler unchanged...

    async def voice_intent(request: web.Request) -> web.Response:
        if dispatcher is None:
            return web.json_response({"error": "voice control disabled"}, status=404)
        if request.headers.get("X-Voice-Token") != voice_token:
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await request.json()
        ok = await dispatcher.dispatch(
            int(body["guild_id"]), body.get("intent", ""), body.get("arg")
        )
        log.info("voice intent %s(%s) guild=%s -> %s",
                 body.get("intent"), body.get("arg"), body.get("guild_id"), ok)
        return web.json_response({"ok": ok}, status=200 if ok else 422)

    app.add_routes([web.post("/voice-intent", voice_intent)])
```
Update `start_health_server(bot, service, port, dispatcher=None, voice_token="")` to pass them through, and in `core/runtime.py` construct `VoiceIntentDispatcher(service, repo)` + `ListenerNotifier(...)` only `if settings.voice_control_enabled`, attach the notifier to the service (`service.voice_notifier = notifier or None`).

- [ ] **Step 7: Session hooks** in `audio/player.py` — end of `begin_session` (after `self.start_listener(guild_id)`) and end of `teardown_session`:

```python
        # begin_session, before `return code`:
        if getattr(self, "voice_notifier", None):
            asyncio.create_task(
                self.voice_notifier.session_started(guild.id, str(voice_channel.id))
            )
```
```python
        # teardown_session, with the other cleanup:
        if getattr(self, "voice_notifier", None):
            asyncio.create_task(self.voice_notifier.session_ended(guild_id))
```

- [ ] **Step 8: `j!wake` cog** `src/jacky/commands/wake.py`:

```python
"""j!wake — show or set this server's voice wake phrase."""

import logging

from discord.ext import commands

from jacky.commands.embeds import error_embed, success_embed

log = logging.getLogger("jacky.commands.wake")


class Wake(commands.Cog):
    def __init__(self, bot, repo, notifier):
        self.bot, self.repo, self.notifier = bot, repo, notifier

    @commands.command(name="wake", brief="Show or set the voice wake phrase")
    @commands.has_guild_permissions(manage_guild=True)
    async def wake(self, ctx: commands.Context, *, phrase: str = "") -> None:
        sid = str(ctx.guild.id)
        if not phrase:
            state = await self.repo.get_state(sid) or {}
            current = state.get("wakePhrase") or "hey jacky"
            await ctx.send(embed=success_embed(f'Wake phrase: **"{current}"**'))
            return
        verdict = await self.notifier.validate_phrase(phrase)
        if verdict is None:
            await ctx.send(embed=error_embed(
                "Voice control is offline right now — try again later."
            ))
            return
        if not verdict["ok"]:
            await ctx.send(embed=error_embed(
                "Can't use that phrase: " + "; ".join(verdict["problems"])
            ))
            return
        await self.repo.update_state(sid, {"wakePhrase": phrase.lower().strip()})
        # Re-push to the listener if a session is live so it takes effect now.
        state = await self.repo.get_state(sid) or {}
        if state.get("voiceChannelId") and ctx.voice_client:
            await self.notifier.session_started(ctx.guild.id, state["voiceChannelId"])
        await ctx.send(embed=success_embed(f'Wake phrase set to **"{phrase}"**'))


async def setup(bot: commands.Bot) -> None:
    if getattr(bot, "voice_notifier", None):
        await bot.add_cog(Wake(bot, bot.repo, bot.voice_notifier))
```
Load it wherever the other cogs load in `core/bot.py`/`runtime.py` (same `load_extension` list), guarded by the flag.

- [ ] **Step 9: Full bot suite green:** `cd services/bot && pytest -q && ruff check .` → PASS (existing tests unaffected: new args are defaulted).
- [ ] **Step 10: Commit:** `git commit -am "feat(bot): /voice-intent endpoint, session hooks, j!wake"`

---

### Task 10: Container, compose, env contract, CI

**Files:** Create `services/voice-listener/Dockerfile`; Modify `deploy/docker-compose.yml`, `deploy/.env.example`, `.github/workflows/integration.yml` (optional smoke), root `Makefile` (verify globs)

- [ ] **Step 1: Dockerfile** (mirror `services/bot/Dockerfile` layout; adds ffmpeg + model download):

```dockerfile
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libopus0 unzip curl && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /tmp/model.zip \
        https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip \
    && unzip -q /tmp/model.zip -d /models \
    && mv /models/vosk-model-small-en-us-0.15 /models/vosk-small-en \
    && rm /tmp/model.zip
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY assets ./assets
RUN pip install --no-cache-dir .
RUN python -c "import ears.gateway, ears.api"    # import smoke at build time
CMD ["python", "-m", "ears"]
```

- [ ] **Step 2: Compose service** (append to `deploy/docker-compose.yml` services; note `profiles` is the kill-switch):

```yaml
  voice-listener:
    # Voice control companion ("Jacky Ears"). Entire feature is opt-in:
    # started only with COMPOSE_PROFILES=voice (VOICE_CONTROL_ENABLED on the
    # bot side). Hard-capped so STT can never starve the music stack.
    build: ../services/voice-listener
    profiles: ["voice"]
    restart: unless-stopped
    mem_limit: 400m
    cpus: 0.5
    environment:
      # NOT `:?set in .env` — compose interpolates the whole file even for
      # profiled-out services, so a `:?` guard aborts the DEFAULT stack boot
      # when unset. Optional here; the listener is crash-only on empty tokens
      # (fails fast, contained to this container) when the voice profile is on.
      DISCORD_EARS_TOKEN: ${DISCORD_EARS_TOKEN:-}
      VOICE_INTERNAL_TOKEN: ${VOICE_INTERNAL_TOKEN:-}
      BOT_INTENT_URL: http://bot:8080/voice-intent
    expose:
      - "8090"
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request;urllib.request.urlopen('http://localhost:8090/health')\""]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 30s
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "3"
    networks: [jacky]
```
And on the existing `bot:` service add:
```yaml
      VOICE_CONTROL_ENABLED: ${VOICE_CONTROL_ENABLED:-false}
      VOICE_LISTENER_URL: http://voice-listener:8090
      VOICE_INTERNAL_TOKEN: ${VOICE_INTERNAL_TOKEN:-}
```

- [ ] **Step 3: `.env.example`** — append with inline docs (repo convention):

```bash
# --- Voice control (optional; entire feature off unless BOTH set) ----------
# Start the listener with: COMPOSE_PROFILES=voice docker compose up -d
VOICE_CONTROL_ENABLED=false
# Second Discord application token for "Jacky Ears" (Developer Portal; needs
# Connect/Speak perms + voice states intent; invite it to your servers).
DISCORD_EARS_TOKEN=
# Shared secret for bot <-> listener HTTP on the internal network.
VOICE_INTERNAL_TOKEN=
```

- [ ] **Step 4: Verify:** `docker compose -f deploy/docker-compose.yml --env-file deploy/.env.example config --quiet` → OK (listener excluded without profile); `COMPOSE_PROFILES=voice docker compose ... config --quiet` with dummy tokens → OK.
- [ ] **Step 5: Makefile/CI:** confirm `make test`/`make lint` glob `services/*` (they iterate service dirs — verify voice-listener is picked up; if the workflows enumerate services explicitly, add it). CI needs no Discord secrets: unit tests only.
- [ ] **Step 6: Commit:** `git commit -am "feat(deploy): voice-listener service behind voice profile"`

---

### Task 11: Docs + soak checklist

**Files:** Modify `docs/STATUS.md` (feature entry), Create `docs/operations/voice-control-runbook.md`

- [ ] **Step 1: Runbook** covering: creating the Jacky Ears Discord app (intents: voice states; invite URL scopes), enabling (`VOICE_CONTROL_ENABLED=true`, `COMPOSE_PROFILES=voice`, `make up`), disabling (unset both — container stops, bot goes dormant), full removal (`git revert -m 1 <merge>`), privacy note (in-memory transcription, nothing persisted), and the soak checklist:

```markdown
## Soak checklist (test guild, before merging to master)
- [ ] j!start in voice, say "hey jacky" → ack chime within ~1s
- [ ] "hey jacky … skip" → confirm blip, track skips
- [ ] pause / resume / volume up / volume down / stop each work
- [ ] "hey jacky … play <song name>" queues a plausible track
- [ ] gibberish after wake → error buzz, music continues
- [ ] j!wake "okay jacky" → takes effect without restart; j!wake bad word rejected
- [ ] kill voice-listener container → music unaffected; j!wake reports offline
- [ ] docker stats: listener RSS < 400m, lavalink/bot steady
```

- [ ] **Step 2: Commit:** `git commit -am "docs: voice control runbook + soak checklist"`
- [ ] **Step 3: Final gate:** `make test && make lint` from repo root → green. Feature stays on `feat/voice-control` until the soak checklist passes in the test guild.

---

## Execution: hierarchical multi-agent strategy

Orchestrator (this session) dispatches one fresh subagent per task with only: the task text, the shared contract block, and the file map. Review diffs between tasks. Order/parallelism:

- Wave 1 (parallel-safe, disjoint files): Task 1 → then Tasks 2, 3, 5, 6 in parallel
- Wave 2: Task 4 (needs 2+3), Task 7 (needs 3)
- Wave 3: Task 8 (needs 4,5,6,7), Task 9 (independent of 2–8 except the contract — may run in Wave 2)
- Wave 4: Task 10, then Task 11

## Self-review (done)

- Spec coverage: receive path (T8), STT/wake (T3–4), earcons (T6/T8), commands incl. play (T2/T9), j!wake + Firestore + validation (T9 + T7), kill-switch/profile (T10), revert branch (header), tests (T2–5,7,9), docs/soak (T11). ✔
- No placeholders; types consistent (`Intent(name, arg)`, event tuples, contract JSON) across tasks. ✔
