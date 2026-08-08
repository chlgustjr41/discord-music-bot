# Voice Command Key Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A push-to-talk Stream Deck key that transcribes speech and runs playback commands ("skip", "play playlist chill", or any song name), logs each one to the dashboard's Command History, and ships with ffmpeg bundled so nothing needs installing.

**Architecture:** The plugin captures the mic with a bundled ffmpeg and POSTs a WAV to one new guarded route on the bot. The bot transcribes via OpenAI, parses intent with a deterministic matcher (no LLM), dispatches onto `PlayerService`, and logs the result. Two independent Now Playing fixes (smooth title scroll, correct artwork ratio) ride along at the end.

**Tech Stack:** unchanged — Python 3.11 / aiohttp / pytest; TypeScript / `@elgato/streamdeck` 1.x / vitest; React (frontend).

**Spec:** `docs/superpowers/specs/2026-08-08-streamdeck-voice-command-design.md` — read it first; it governs.

**House rules (every task):** TDD — write the test, watch it fail for the right reason, implement, watch it pass. Bot gates: `cd services/bot && py -m pytest -q` and `uvx ruff@0.15.20 check src tests`. Plugin gates: `cd streamdeck-plugin && npm test && npm run build`. Frontend gate: `cd frontend && npx tsc -b`. Commit per task with the given message. Use the `py` launcher (plain `python` lacks pytest). Never run `npx @elgato/cli pack` mid-plan — it reformats `manifest.json`.

**Baselines at plan time:** bot 143 tests; plugin 33 tests; manifest Version `0.3.0.0`; auth sweep asserts 10 guarded routes.

**Scope note:** Tasks 1–10 are the voice feature. **Task 11 is independent** (Now Playing polish) and could ship on its own; it is included here because it is two small changes requested alongside.

---

## File Structure

**Bot (`services/bot/`):**
| File | Change | Responsibility |
|---|---|---|
| `src/jacky/config.py` | modify | `openai_api_key`, `openai_stt_model` |
| `src/jacky/api/voice_intent.py` | create | pure grammar → `Intent` |
| `src/jacky/api/transcribe.py` | create | injectable OpenAI STT client |
| `src/jacky/voice_control.py` | create | `VoiceIntentDispatcher` (ported) |
| `src/jacky/state/repository.py` | modify | `source`/`transcript` on command log + dedupe fix |
| `src/jacky/api/control.py` | modify | `POST /control/voice` |
| `src/jacky/core/bot.py` | modify | construct transcriber + dispatcher |
| `tests/conftest.py` | modify | FakeRepo.log_command signature |
| `tests/test_voice_intent.py`, `tests/test_voice_control.py` | create | parser + dispatcher |
| `tests/test_control_api.py` | modify | route tests, auth sweep 10 → 11 |

**Frontend (`frontend/`):** `src/types.ts` (+2 optional fields), `src/components/CommandHistory.tsx` (voice rendering).

**Plugin (`streamdeck-plugin/`):** `scripts/fetch-ffmpeg.mjs`, `src/ffmpeg-path.ts`, `src/audio-capture.ts`, `src/actions/voice.ts`, `src/image.ts` (letterbox), `src/actions/now-playing.ts`, `src/api-client.ts`, `src/pi-bridge.ts`, `src/plugin.ts`, `ui/settings.html`, `manifest.json`, `imgs/voice.svg`, `.gitignore`; tests `tests/voice-intent-free`, `tests/audio-capture.test.ts`, `tests/image.test.ts`.

---

## Part 1 — Bot

### Task 1: Config for OpenAI

**Files:** `services/bot/src/jacky/config.py`, `services/bot/tests/test_config.py`

- [ ] **Step 1: Write the failing test.** Append to `services/bot/tests/test_config.py`:

```python
def test_openai_settings_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty must behave like unset: compose passes optional vars as ${VAR:-},
    which SETS them to an empty string, so a .get() default never fires."""
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_STT_MODEL", raising=False)
    s = Settings.from_env()
    assert s.openai_api_key == ""
    assert s.openai_stt_model == "gpt-4o-mini-transcribe"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_STT_MODEL", "")
    s = Settings.from_env()
    assert s.openai_api_key == "sk-test"
    assert s.openai_stt_model == "gpt-4o-mini-transcribe"

    monkeypatch.setenv("OPENAI_STT_MODEL", "whisper-1")
    assert Settings.from_env().openai_stt_model == "whisper-1"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/bot && py -m pytest tests/test_config.py -q`
Expected: FAIL — `AttributeError: openai_api_key`.

- [ ] **Step 3: Implement.** In `services/bot/src/jacky/config.py`, add two fields after `public_control_url: str`:

```python
    openai_api_key: str
    openai_stt_model: str
```

and in `from_env()`, after the `public_control_url=` entry:

```python
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            # `or` (not a .get default): compose passes ${VAR:-} as an empty
            # string, so a .get default would never fire.
            openai_stt_model=(
                os.environ.get("OPENAI_STT_MODEL") or "gpt-4o-mini-transcribe"
            ),
```

- [ ] **Step 4: Verify.** `py -m pytest -q` (expect 144) and `uvx ruff@0.15.20 check src tests`.

- [ ] **Step 5: Commit**

```bash
git add services/bot/src/jacky/config.py services/bot/tests/test_config.py
git commit -m "feat(voice): openai transcription settings"
```

### Task 2: Intent parser (pure)

**Files:** `services/bot/src/jacky/api/voice_intent.py`, `services/bot/tests/test_voice_intent.py`

This is the heart of the feature and the biggest test surface. Note the deliberate asymmetry: **matching** uses a fully normalized string, but the **argument** is taken from the original text, so a query like `AC/DC` keeps its punctuation.

- [ ] **Step 1: Write the failing tests.** Create `services/bot/tests/test_voice_intent.py`:

```python
"""Voice grammar: ordered matching, with song search as the only free-form case."""

import pytest

from jacky.api.voice_intent import Intent, normalize_playlist_name, parse_intent


@pytest.mark.parametrize(
    ("said", "kind"),
    [
        ("skip", "skip"),
        ("Skip.", "skip"),
        ("next", "skip"),
        ("skip track", "skip"),
        ("pause", "pause"),
        ("resume", "resume"),
        ("unpause", "resume"),
        ("continue", "resume"),
        ("volume up", "volume_up"),
        ("louder", "volume_up"),
        ("turn it up", "volume_up"),
        ("volume down", "volume_down"),
        ("quieter", "volume_down"),
        ("turn it down", "volume_down"),
    ],
)
def test_exact_commands(said, kind):
    assert parse_intent(said) == Intent(kind, "")


@pytest.mark.parametrize(
    ("said", "kind", "arg"),
    [
        ("play playlist chill vibes", "playlist_play", "chill vibes"),
        ("Play playlist Chill Vibes", "playlist_play", "Chill Vibes"),
        ("add playlist chill", "playlist_add", "chill"),
        ("queue playlist chill", "playlist_add", "chill"),
    ],
)
def test_playlist_commands(said, kind, arg):
    assert parse_intent(said) == Intent(kind, arg)


@pytest.mark.parametrize(
    ("said", "arg"),
    [
        ("play bohemian rhapsody", "bohemian rhapsody"),
        ("add bohemian rhapsody", "bohemian rhapsody"),
        ("queue bohemian rhapsody", "bohemian rhapsody"),
        # The free-form case: no verb at all.
        ("bohemian rhapsody", "bohemian rhapsody"),
        # "playlist" only counts directly after the verb.
        ("play the playlist song", "the playlist song"),
    ],
)
def test_search(said, arg):
    assert parse_intent(said) == Intent("search", arg)


def test_query_keeps_original_punctuation_and_case():
    """Matching normalizes; the ARGUMENT must not — 'AC/DC' is a real band."""
    assert parse_intent("play AC/DC Back in Black") == Intent(
        "search", "AC/DC Back in Black"
    )


def test_trailing_sentence_punctuation_is_stripped_from_queries():
    assert parse_intent("play bohemian rhapsody.") == Intent(
        "search", "bohemian rhapsody"
    )


def test_empty_transcript_is_none():
    assert parse_intent("") is None
    assert parse_intent("   ") is None
    assert parse_intent("...") is None


def test_bare_verb_is_not_a_command():
    """'play' with nothing after it is not a search for the empty string."""
    assert parse_intent("play") == Intent("search", "play")
    assert parse_intent("play playlist") == Intent("search", "play playlist")


def test_stop_is_not_a_voice_command():
    """Excluded by design: one misrecognition would clear the queue."""
    assert parse_intent("stop") == Intent("search", "stop")


@pytest.mark.parametrize(
    ("a", "b"),
    [("Chill Vibes", "chill vibes"), ("Late-Night!", "latenight"), ("A B", "ab")],
)
def test_playlist_name_normalization_matches_loosely(a, b):
    assert normalize_playlist_name(a) == normalize_playlist_name(b)
```

- [ ] **Step 2: Run to verify failure**

Run: `py -m pytest tests/test_voice_intent.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'jacky.api.voice_intent'`.

- [ ] **Step 3: Implement.** Create `services/bot/src/jacky/api/voice_intent.py`:

```python
"""Voice grammar: transcript -> Intent.

Deliberately a deterministic ordered matcher rather than an LLM. The
requirement is structured phrases with consistent behavior, and a table is
free, instant, and testable. Song search is the ONE free-form case: anything
matching no command becomes a search query.

Note the asymmetry: matching uses a fully normalized string (punctuation
stripped) but the ARGUMENT is sliced from the original text, so a query like
"AC/DC" survives intact.
"""

import re
from dataclasses import dataclass

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9]")


@dataclass(frozen=True)
class Intent:
    kind: str  # skip|pause|resume|volume_up|volume_down|playlist_play|playlist_add|search
    arg: str = ""


_EXACT = {
    "skip": "skip",
    "next": "skip",
    "skip track": "skip",
    "pause": "pause",
    "resume": "resume",
    "unpause": "resume",
    "continue": "resume",
    "volume up": "volume_up",
    "louder": "volume_up",
    "turn it up": "volume_up",
    "volume down": "volume_down",
    "quieter": "volume_down",
    "turn it down": "volume_down",
}

# Checked before the plain verbs, so "playlist" right after the verb wins.
_PLAYLIST_PREFIXES = (
    ("play playlist ", "playlist_play"),
    ("add playlist ", "playlist_add"),
    ("queue playlist ", "playlist_add"),
)
_SEARCH_PREFIXES = ("play ", "add ", "queue ")


def normalize_playlist_name(name: str) -> str:
    """Loose key for matching spoken names to saved ones: 'Chill Vibes' and
    'chill vibes' must be the same playlist."""
    return _NON_ALNUM.sub("", name.lower())


def parse_intent(transcript: str) -> Intent | None:
    """None when there is nothing to act on (silence or pure punctuation)."""
    text = transcript.strip().strip(".!?,").strip()
    if not text:
        return None

    lowered = text.lower()
    # Normalized only for MATCHING; arguments are sliced from `text` below so
    # a query like "AC/DC" keeps its punctuation.
    norm = _WS.sub(" ", _PUNCT.sub(" ", lowered)).strip()
    if norm in _EXACT:
        return Intent(_EXACT[norm])

    for prefix, kind in _PLAYLIST_PREFIXES:
        if lowered.startswith(prefix):
            arg = text[len(prefix):].strip()
            if arg:
                return Intent(kind, arg)

    for prefix in _SEARCH_PREFIXES:
        if lowered.startswith(prefix):
            arg = text[len(prefix):].strip()
            # "<verb> playlist" with no name is an INCOMPLETE playlist command,
            # not a search for the literal word "playlist": fall through to the
            # whole-transcript case so the user hears "No results" for what
            # they actually said.
            if arg and arg.lower() != "playlist":
                return Intent("search", arg)

    # The one free-form case.
    return Intent("search", text)
```

- [ ] **Step 4: Verify.** `py -m pytest -q` (expect 144 + 30 parametrized cases; report the exact number) and ruff.

- [ ] **Step 5: Commit**

```bash
git add services/bot/src/jacky/api/voice_intent.py services/bot/tests/test_voice_intent.py
git commit -m "feat(voice): deterministic intent grammar"
```

### Task 3: Transcription client

**Files:** `services/bot/src/jacky/api/transcribe.py`, `services/bot/tests/test_voice_intent.py` (append)

- [ ] **Step 1: Write the failing tests.** Append to `services/bot/tests/test_voice_intent.py`:

```python
# ── transcription client ─────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status, payload):
        self.status, self._payload = status, payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeHttp:
    def __init__(self, response):
        self._response, self.calls = response, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


async def test_transcriber_posts_audio_and_returns_text():
    from jacky.api.transcribe import OpenAITranscriber

    http = _FakeHttp(_FakeResponse(200, {"text": "  play bohemian rhapsody "}))
    t = OpenAITranscriber(http, "sk-test", "gpt-4o-mini-transcribe")
    assert await t.transcribe(b"RIFFfake") == "play bohemian rhapsody"

    url, kwargs = http.calls[0]
    assert url.endswith("/audio/transcriptions")
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"


async def test_transcriber_raises_on_non_200():
    from jacky.api.transcribe import OpenAITranscriber, TranscribeError

    t = OpenAITranscriber(_FakeHttp(_FakeResponse(500, {})), "sk", "m")
    with pytest.raises(TranscribeError):
        await t.transcribe(b"RIFFfake")
```

- [ ] **Step 2: Run to verify failure**

Run: `py -m pytest tests/test_voice_intent.py -q -k transcriber`
Expected: FAIL — no module `jacky.api.transcribe`.

- [ ] **Step 3: Implement.** Create `services/bot/src/jacky/api/transcribe.py`:

```python
"""OpenAI speech-to-text. Injectable so tests never touch the network.

Audio is streamed straight through — never written to disk here or anywhere
else in the request path.
"""

from typing import Any

import aiohttp

URL = "https://api.openai.com/v1/audio/transcriptions"


class TranscribeError(Exception):
    pass


class OpenAITranscriber:
    def __init__(self, http: Any, api_key: str, model: str) -> None:
        self.http, self.api_key, self.model = http, api_key, model

    async def transcribe(self, wav: bytes) -> str:
        form = aiohttp.FormData()
        form.add_field("file", wav, filename="audio.wav", content_type="audio/wav")
        form.add_field("model", self.model)
        try:
            async with self.http.post(
                URL, data=form, headers={"Authorization": f"Bearer {self.api_key}"}
            ) as resp:
                if resp.status != 200:
                    raise TranscribeError(f"transcription failed: {resp.status}")
                body = await resp.json()
        except TranscribeError:
            raise
        except Exception as exc:  # noqa: BLE001 — network faults are one failure
            raise TranscribeError(f"transcription request failed: {exc}") from exc
        return (body.get("text") or "").strip()
```

- [ ] **Step 4: Verify.** `py -m pytest -q` and ruff.

- [ ] **Step 5: Commit**

```bash
git add services/bot/src/jacky/api/transcribe.py services/bot/tests/test_voice_intent.py
git commit -m "feat(voice): injectable openai transcription client"
```

### Task 4: Command-history source and transcript

**Files:** `services/bot/src/jacky/state/repository.py`, `services/bot/tests/conftest.py`, `services/bot/tests/test_voice_control.py` (create)

The dedupe fix matters: `_log_command` currently matches on `(command, args)` only, so a voice `play X` would merge into an existing Discord `play X` row and silently relabel it. Filtering in Python (rather than adding a third `where`) is deliberate — a Firestore equality filter does **not** match documents where the field is absent, and every existing row predates `source`.

- [ ] **Step 1: Write the failing test.** Create `services/bot/tests/test_voice_control.py`:

```python
"""Voice dispatch onto PlayerService, and voice command-history logging."""

from tests.conftest import FakeRepo


async def test_fake_repo_records_source_and_transcript():
    repo = FakeRepo()
    await repo.log_command("1", "play", "x", "Me", "42")
    await repo.log_command(
        "1", "play", "x", "Me", "42", source="voice", transcript="play x"
    )
    assert repo.command_log[0][4:] == ("discord", "")
    assert repo.command_log[1][4:] == ("voice", "play x")
```

- [ ] **Step 2: Run to verify failure**

Run: `py -m pytest tests/test_voice_control.py -q`
Expected: FAIL — `log_command() got an unexpected keyword argument 'source'`.

- [ ] **Step 3: Implement — FakeRepo.** In `services/bot/tests/conftest.py`, replace `log_command`:

```python
    async def log_command(
        self, sid, command, args, user, user_id, *, source="discord", transcript=""
    ):
        self.command_log.append((sid, command, args, user, source, transcript))
```

- [ ] **Step 4: Implement — repository.** In `services/bot/src/jacky/state/repository.py`, replace `_log_command`/`log_command`:

```python
    def _log_command(
        self, server_id: str, command: str, args: str, user: str, user_id: str,
        source: str, transcript: str,
    ) -> None:
        coll = self.db.collection("servers").document(server_id).collection("commandHistory")
        # Source is filtered in Python, not with a third `where`: a Firestore
        # equality filter never matches documents MISSING the field, and every
        # row written before this feature has no `source`. Without this split,
        # a voice "play X" would merge into the Discord "play X" row and
        # relabel it.
        existing = [
            d for d in coll.where("command", "==", command)
                           .where("args", "==", args).limit(10).stream()
            if (d.to_dict().get("source") or "discord") == source
        ]
        if existing:
            existing[0].reference.update({
                "user": user,
                "userId": user_id,
                "timestamp": firestore.SERVER_TIMESTAMP,
                "callCount": firestore.Increment(1),
                "source": source,
                "transcript": transcript,
            })
        else:
            coll.add({
                "command": command,
                "args": args,
                "user": user,
                "userId": user_id,
                "timestamp": firestore.SERVER_TIMESTAMP,
                "callCount": 1,
                "source": source,
                "transcript": transcript,
            })

    async def log_command(
        self, server_id: str, command: str, args: str, user: str, user_id: str,
        *, source: str = "discord", transcript: str = "",
    ) -> None:
        await self._run(
            self._log_command, server_id, command, args, user, user_id,
            source, transcript,
        )
```

- [ ] **Step 5: Verify.** `py -m pytest -q` (existing command-logging callers pass 5 positional args and are unaffected) and ruff.

- [ ] **Step 6: Commit**

```bash
git add services/bot/src/jacky/state/repository.py services/bot/tests/conftest.py services/bot/tests/test_voice_control.py
git commit -m "feat(voice): command history records source and transcript"
```

### Task 5: VoiceIntentDispatcher

**Files:** `services/bot/src/jacky/voice_control.py`, `services/bot/tests/test_voice_control.py`

Ported from `feat/voice-control` with `stop` removed and the two playlist intents added.

- [ ] **Step 1: Write the failing tests.** Append to `services/bot/tests/test_voice_control.py`:

```python
# ── dispatcher ───────────────────────────────────────────────────────────

import pytest

from jacky.api.voice_intent import Intent


@pytest.fixture
def dispatcher(service):
    from jacky.voice_control import VoiceIntentDispatcher

    return VoiceIntentDispatcher(service, service.repo)


async def test_media_intents_call_the_player(dispatcher, service, guild_id, sid):
    await service.repo.update_state(sid, {"volume": 50})

    assert (await dispatcher.dispatch(guild_id, Intent("skip"))).ok
    assert service.node.updates[-1] == (guild_id, {"track": {"encoded": None}})

    assert (await dispatcher.dispatch(guild_id, Intent("pause"))).ok
    assert (await service.repo.get_state(sid))["isPaused"] is True

    assert (await dispatcher.dispatch(guild_id, Intent("resume"))).ok
    assert (await service.repo.get_state(sid))["isPaused"] is False

    await dispatcher.dispatch(guild_id, Intent("volume_up"))
    assert (await service.repo.get_state(sid))["volume"] == 60
    await dispatcher.dispatch(guild_id, Intent("volume_down"))
    assert (await service.repo.get_state(sid))["volume"] == 50


async def test_search_queues_a_track(dispatcher, service, guild_id, sid):
    await service.repo.update_state(sid, {"currentTrack": {"title": "Now"}})
    result = await dispatcher.dispatch(guild_id, Intent("search", "a song"))
    assert result.ok
    assert [t["title"] for t in (await service.repo.get_state(sid))["queue"]] == ["Song"]
    assert result.detail == "Song"


async def test_search_with_no_results_is_not_ok(dispatcher, service, guild_id):
    from jacky.audio.models import LoadResult

    service.node.default_result = LoadResult(kind="empty", tracks=[])
    result = await dispatcher.dispatch(guild_id, Intent("search", "nothing"))
    assert result.ok is False
    assert "No results" in result.detail


async def test_playlist_play_jumps_to_the_front(dispatcher, service, guild_id, sid):
    await service.repo.save_playlist(
        sid, "Chill Vibes", [{"title": "P1"}, {"title": "P2"}], "me"
    )
    await service.repo.update_state(
        sid, {"queue": [{"title": "Old"}], "currentTrack": {"title": "Now"}}
    )
    # Spoken loosely: normalization must still find "Chill Vibes".
    result = await dispatcher.dispatch(guild_id, Intent("playlist_play", "chill vibes"))
    assert result.ok
    queue = (await service.repo.get_state(sid))["queue"]
    assert [t["title"] for t in queue] == ["P1", "P2", "Old"]
    assert service.node.updates[-1] == (guild_id, {"track": {"encoded": None}})


async def test_playlist_add_appends_without_interrupting(
    dispatcher, service, guild_id, sid
):
    await service.repo.save_playlist(sid, "Chill", [{"title": "P1"}], "me")
    await service.repo.update_state(
        sid, {"queue": [{"title": "Old"}], "currentTrack": {"title": "Now"}}
    )
    before = len(service.node.updates)
    result = await dispatcher.dispatch(guild_id, Intent("playlist_add", "chill"))
    assert result.ok
    queue = (await service.repo.get_state(sid))["queue"]
    assert [t["title"] for t in queue] == ["Old", "P1"]
    # Appending must never interrupt what is playing.
    assert len(service.node.updates) == before


async def test_unknown_playlist_is_not_ok(dispatcher, service, guild_id):
    result = await dispatcher.dispatch(guild_id, Intent("playlist_play", "nope"))
    assert result.ok is False
    assert "nope" in result.detail


async def test_stop_intent_is_not_dispatchable(dispatcher, guild_id):
    """stop is excluded from voice by design."""
    result = await dispatcher.dispatch(guild_id, Intent("stop"))
    assert result.ok is False
```

- [ ] **Step 2: Run to verify failure**

Run: `py -m pytest tests/test_voice_control.py -q`
Expected: FAIL — no module `jacky.voice_control`.

- [ ] **Step 3: Implement.** Create `services/bot/src/jacky/voice_control.py`:

```python
"""Dispatch parsed voice intents onto PlayerService.

Ported from the shelved feat/voice-control branch — the layer that always
worked; only the Discord voice-receive acquisition below it ever failed.
`stop` is deliberately absent: one misrecognition would end the session and
clear the queue with no undo, and a dedicated Stop key exists.
"""

import logging
from dataclasses import dataclass
from typing import Any

from jacky.api.voice_intent import Intent, normalize_playlist_name
from jacky.audio.models import to_track_data

log = logging.getLogger("jacky.voice")

VOLUME_STEP = 10


@dataclass(frozen=True)
class DispatchResult:
    ok: bool
    detail: str = ""


class VoiceIntentDispatcher:
    def __init__(self, service: Any, repo: Any) -> None:
        self.service, self.repo = service, repo

    async def dispatch(self, guild_id: int, intent: Intent) -> DispatchResult:
        sid = str(guild_id)
        kind = intent.kind
        if kind == "skip":
            await self.service.skip(guild_id)
            return DispatchResult(True, "Skipped")
        if kind == "pause":
            await self.service.pause(guild_id, True)
            return DispatchResult(True, "Paused")
        if kind == "resume":
            await self.service.pause(guild_id, False)
            return DispatchResult(True, "Resumed")
        if kind in ("volume_up", "volume_down"):
            state = await self.repo.get_state(sid) or {}
            current = state.get("volume")
            current = 80 if current is None else int(current)
            step = VOLUME_STEP if kind == "volume_up" else -VOLUME_STEP
            new = await self.service.set_volume(guild_id, current + step)
            return DispatchResult(True, f"Volume {new}")
        if kind in ("playlist_play", "playlist_add") and intent.arg:
            return await self._playlist(guild_id, sid, intent)
        if kind == "search" and intent.arg:
            return await self._search(guild_id, sid, intent.arg)
        return DispatchResult(False, "Unknown command")

    async def _search(self, guild_id: int, sid: str, query: str) -> DispatchResult:
        try:
            result = await self.service.resolve(query)
        except Exception as exc:  # noqa: BLE001 — surfaced on the key
            log.warning("voice search failed for %r: %s", query, exc)
            return DispatchResult(False, "Search failed")
        if not result.tracks:
            return DispatchResult(False, f"No results for {query}")
        td = to_track_data(result.first, "voice command")
        state = await self.repo.get_state(sid) or {}
        if state.get("currentTrack"):
            await self.repo.add_to_queue(sid, td)
            return DispatchResult(True, td["title"])
        ok = await self.service.start_current_track(guild_id, result.first, td)
        return DispatchResult(bool(ok), td["title"] if ok else "Playback failed")

    async def _playlist(self, guild_id: int, sid: str, intent: Intent) -> DispatchResult:
        wanted = normalize_playlist_name(intent.arg)
        saved = await self.repo.list_playlists(sid)
        match = next(
            (p for p in saved if normalize_playlist_name(p.get("name", "")) == wanted),
            None,
        )
        tracks = (match or {}).get("tracks") or []
        if not tracks:
            return DispatchResult(False, f"No playlist called {intent.arg}")

        queued = [{**t, "requestedBy": "voice command"} for t in tracks]
        existing = await self.repo.get_queue(sid)
        # Decide BEFORE the write: the queue write is what wakes the Firestore
        # listener, and listener.py auto-starts playback when it sees the queue
        # grow while idle. Any await between the write and the start call is a
        # window for it to pop the track we just inserted.
        playing = bool((await self.repo.get_state(sid) or {}).get("currentTrack"))
        if intent.kind == "playlist_play":
            await self.repo.update_state(sid, {"queue": [*queued, *existing]})
            if playing:
                await self.service.skip(guild_id)
            else:
                await self.service.play_next(guild_id)
        else:
            await self.repo.update_state(sid, {"queue": [*existing, *queued]})
            # Appending must never interrupt the current track.
            if not playing:
                await self.service.play_next(guild_id)
        name = match.get("name", intent.arg)
        return DispatchResult(True, f"{name} ({len(queued)})")
```

- [ ] **Step 4: Verify.** `py -m pytest -q` and ruff.

- [ ] **Step 5: Commit**

```bash
git add services/bot/src/jacky/voice_control.py services/bot/tests/test_voice_control.py
git commit -m "feat(voice): intent dispatcher with playlist support"
```

### Task 6: `POST /control/voice`

**Files:** `services/bot/src/jacky/api/control.py`, `services/bot/tests/test_control_api.py`

- [ ] **Step 1: Write the failing tests.** Append to `services/bot/tests/test_control_api.py`:

```python
# ── voice command route ──────────────────────────────────────────────────

WAV = b"RIFF" + b"\x00" * 200


async def test_voice_requires_a_live_session_before_transcribing(
    client, service, auth, transcriber
):
    """409 must come BEFORE transcription — never pay for a doomed request."""
    resp = await client.post("/control/voice", data=WAV, headers=auth)
    assert resp.status == 409
    assert transcriber.calls == []


async def test_voice_runs_the_recognized_command(
    client, service, guild_id, sid, auth, transcriber
):
    put_user_in_voice(service, guild_id)
    transcriber.text = "skip"
    resp = await client.post("/control/voice", data=WAV, headers=auth)
    body = await resp.json()
    assert resp.status == 200
    assert body["transcript"] == "skip"
    assert body["intent"] == "skip"
    assert body["ok"] is True
    assert service.node.updates[-1] == (guild_id, {"track": {"encoded": None}})


async def test_voice_logs_to_command_history_with_transcript(
    client, service, guild_id, sid, auth, transcriber
):
    put_user_in_voice(service, guild_id)
    transcriber.text = "play a song"
    await client.post("/control/voice", data=WAV, headers=auth)
    entry = service.repo.command_log[-1]
    assert entry[1] == "play"            # executed action, so retrigger works
    assert entry[2] == "a song"
    assert entry[4] == "voice"
    assert entry[5] == "play a song"     # the recognized speech


async def test_voice_rejects_oversized_bodies(client, service, guild_id, auth):
    put_user_in_voice(service, guild_id)
    resp = await client.post("/control/voice", data=b"\x00" * 700_000, headers=auth)
    assert resp.status == 413


async def test_voice_empty_transcript_is_422(
    client, service, guild_id, auth, transcriber
):
    put_user_in_voice(service, guild_id)
    transcriber.text = "   "
    resp = await client.post("/control/voice", data=WAV, headers=auth)
    assert resp.status == 422
    assert (await resp.json())["error"] == "no-speech"


async def test_voice_transcription_failure_is_502(
    client, service, guild_id, auth, transcriber
):
    from jacky.api.transcribe import TranscribeError

    put_user_in_voice(service, guild_id)
    transcriber.error = TranscribeError("boom")
    resp = await client.post("/control/voice", data=WAV, headers=auth)
    assert resp.status == 502
    assert (await resp.json())["error"] == "stt-failed"
```

- [ ] **Step 2: Add the transcriber fake and wire it into the client fixture.** In `services/bot/tests/test_control_api.py`, add near the other fixtures:

```python
class FakeTranscriber:
    def __init__(self):
        self.text, self.error, self.calls = "skip", None, []

    async def transcribe(self, wav: bytes) -> str:
        self.calls.append(wav)
        if self.error:
            raise self.error
        return self.text


@pytest.fixture
def transcriber():
    return FakeTranscriber()
```

Then, in the existing `client` fixture, pass the transcriber and a dispatcher into `register_control_routes` (add the two keyword arguments to the existing call):

```python
    from jacky.voice_control import VoiceIntentDispatcher

    register_control_routes(
        app,
        bot=service.bot,
        service=service,
        token_store=store,
        limiter=limiter,
        transcriber=transcriber,
        voice_dispatcher=VoiceIntentDispatcher(service, service.repo),
    )
```

and add `transcriber` to the `client` fixture's parameter list so pytest injects it.

- [ ] **Step 3: Bump the auth sweep.** In `test_all_control_routes_require_auth`, raise the expected count 10 → 11 and update its tally comment. The route is registered unconditionally in the test fixture (above), so the sweep genuinely covers it — in production it is gated on the API key.

- [ ] **Step 4: Run to verify failure**

Run: `py -m pytest tests/test_control_api.py -q -k voice`
Expected: FAIL — `register_control_routes() got an unexpected keyword argument 'transcriber'`.

- [ ] **Step 5: Implement.** In `services/bot/src/jacky/api/control.py`, extend the signature:

```python
def register_control_routes(
    app: web.Application, *, bot: Any, service: Any, token_store: Any, limiter: Any,
    transcriber: Any = None, voice_dispatcher: Any = None,
) -> None:
```

Add the constant near `_MEMBER_LOOKUP_ERRORS`:

```python
# 600 KB ~= 18 s of 16 kHz mono WAV, comfortably above the client's 15 s cap.
VOICE_MAX_BYTES = 600_000
```

Add the handler after `dashboard_url`:

```python
    async def voice(request: web.Request, user_id: str) -> web.Response:
        """Transcribe a push-to-talk clip and run the recognized command."""
        if transcriber is None or voice_dispatcher is None:
            return web.json_response({"error": "voice-disabled"}, status=503)
        guild = await resolve_guild(member_id_of(user_id))
        if guild is None:
            # Before transcription: never pay for a request that cannot succeed.
            return web.json_response({"error": "no-active-session"}, status=409)
        audio = await request.read()
        if len(audio) > VOICE_MAX_BYTES:
            return web.json_response({"error": "too-large"}, status=413)

        try:
            transcript = await transcriber.transcribe(audio)
        except Exception:  # noqa: BLE001 — any STT fault is one failure mode
            log.exception("voice transcription failed")
            return web.json_response({"error": "stt-failed"}, status=502)

        intent = parse_intent(transcript)
        if intent is None:
            return web.json_response({"error": "no-speech"}, status=422)

        result = await voice_dispatcher.dispatch(guild.id, intent)
        # Logged as the EXECUTED action so the dashboard's retrigger works,
        # with the transcript alongside it. Transcript persistence is an
        # explicit product decision (spec §Decisions).
        await service.repo.log_command(
            str(guild.id), _LOG_COMMAND_FOR.get(intent.kind, intent.kind),
            intent.arg, "Voice", user_id,
            source="voice", transcript=transcript,
        )
        return web.json_response({
            "transcript": transcript,
            "intent": intent.kind,
            "ok": result.ok,
            "detail": result.detail,
        })
```

Add the intent→command-name map next to `VOICE_MAX_BYTES` (so history rows read like the `j!` commands the dashboard already shows):

```python
_LOG_COMMAND_FOR = {
    "search": "play",
    "playlist_play": "playlist",
    "playlist_add": "playlist",
    "volume_up": "volume",
    "volume_down": "volume",
}
```

Import the parser at the top of the file:

```python
from jacky.api.voice_intent import parse_intent
```

and register the route:

```python
        web.post("/control/voice", guarded(voice)),
```

- [ ] **Step 6: Verify.** `py -m pytest -q` and ruff.

- [ ] **Step 7: Commit**

```bash
git add services/bot/src/jacky/api/control.py services/bot/tests/test_control_api.py
git commit -m "feat(voice): POST /control/voice route"
```

### Task 7: Bot wiring

**Files:** `services/bot/src/jacky/core/bot.py`

- [ ] **Step 1: Construct the transcriber and dispatcher.** In `setup_hook`, inside the existing `if self.settings.discord_client_id and self.settings.discord_client_secret:` block, replace the `register_control_routes(...)` call with:

```python
            transcriber = None
            voice_dispatcher = None
            if self.settings.openai_api_key:
                from jacky.api.transcribe import OpenAITranscriber
                from jacky.voice_control import VoiceIntentDispatcher

                transcriber = OpenAITranscriber(
                    self.http_session,
                    self.settings.openai_api_key,
                    self.settings.openai_stt_model,
                )
                voice_dispatcher = VoiceIntentDispatcher(self.service, self.repo)
            register_control_routes(
                health_app, bot=self, service=self.service,
                token_store=self.token_store, limiter=SlidingWindow(),
                transcriber=transcriber, voice_dispatcher=voice_dispatcher,
            )
```

- [ ] **Step 2: Verify.** `py -m pytest -q`, `uvx ruff@0.15.20 check src tests`, and `py -c "import jacky.core.bot"`.

- [ ] **Step 3: Commit**

```bash
git add services/bot/src/jacky/core/bot.py
git commit -m "feat(voice): wire transcriber and dispatcher when the api key is set"
```

### Task 8: Deploy contract

**Files:** `deploy/docker-compose.yml`, `deploy/.env.example`

- [ ] **Step 1: compose.** In the `bot` service `environment:` block, add:

```yaml
      OPENAI_API_KEY: ${OPENAI_API_KEY:-}
      OPENAI_STT_MODEL: ${OPENAI_STT_MODEL:-}
```

- [ ] **Step 2: `.env.example`.** Append (header padded to the file's 75-character width):

```
# ── Voice command key ────────────────────────────────────────────────────
# OpenAI key for speech-to-text (platform.openai.com -> API keys). Empty =
# the voice route is not registered and the key reports it is unavailable.
# ~$0.003/min: a 5-second command costs a fraction of a cent.
OPENAI_API_KEY=

# Override the transcription model. Default: gpt-4o-mini-transcribe
#OPENAI_STT_MODEL=whisper-1
```

- [ ] **Step 3: Validate.** From `deploy/`: `docker compose --env-file .env.ci-test config --quiet` and `docker compose --env-file .env.example config --quiet` — both exit 0.

- [ ] **Step 4: Commit**

```bash
git add deploy/docker-compose.yml deploy/.env.example
git commit -m "chore(deploy): openai transcription env contract"
```

---

## Part 2 — Frontend

### Task 9: Voice entries in Command History

**Files:** `frontend/src/types.ts`, `frontend/src/components/CommandHistory.tsx`

- [ ] **Step 1: Extend the type.** In `frontend/src/types.ts`, add two optional fields to `CommandHistoryEntry` (optional so existing rows, which have neither, still typecheck):

```ts
  source?: string;      // "voice" | "discord" (absent = discord)
  transcript?: string;  // recognized speech, voice entries only
```

- [ ] **Step 2: Render voice entries distinctly.** In `CommandHistory.tsx`, import `Mic` from `lucide-react` (alongside the existing icon imports). Find the helper that formats an entry as `j!{command} {args}` and make it voice-aware:

```tsx
  const label = (cmd: CommandHistoryEntry) => {
    const prefix = `j!${cmd.command}`;
    return cmd.args ? `${prefix} ${cmd.args}` : prefix;
  };
```

Wherever that label is rendered, render voice entries instead as the recognized speech plus the action it produced, with a mic badge:

```tsx
  {cmd.source === "voice" ? (
    <span className="flex min-w-0 flex-col gap-0.5">
      <span className="flex items-center gap-1.5">
        <Mic className="h-3 w-3 shrink-0 text-primary" />
        <Badge variant="outline" className="px-1 py-0 text-[10px]">
          Voice
        </Badge>
        <span className="truncate italic">"{cmd.transcript}"</span>
      </span>
      <span className="truncate text-xs text-muted-foreground">
        → {label(cmd)}
      </span>
    </span>
  ) : (
    <span className="truncate">{label(cmd)}</span>
  )}
```

Keep the surrounding markup (selection checkbox, timestamp, callCount, retrigger button) unchanged — voice entries stay retriggerable precisely because `command`/`args` hold the executed action.

- [ ] **Step 3: Verify.** `cd frontend && npx tsc -b` (exit 0) and `npm run build`. Confirm with `npm run lint` that no NEW errors appear in the two changed files (the repo has 16 pre-existing errors in other files).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/CommandHistory.tsx
git commit -m "feat(web): show voice commands in command history"
```

---

## Part 3 — Plugin

### Task 10: Bundled ffmpeg + capture + the voice key

**Files:** `scripts/fetch-ffmpeg.mjs`, `src/ffmpeg-path.ts`, `src/audio-capture.ts`, `src/actions/voice.ts`, `src/api-client.ts`, `src/pi-bridge.ts`, `src/plugin.ts`, `ui/settings.html`, `manifest.json`, `imgs/voice.svg`, `.gitignore`, `package.json`, `tests/audio-capture.test.ts`

- [ ] **Step 1: Pin a real ffmpeg build.** The URL and hash below are placeholders **on purpose** — they must be real values you obtained, never guessed, because a fabricated hash either fails the build for the wrong reason or tempts someone to disable the check. Resolve them:

```bash
# Pick a concrete LGPL win64 asset (NOT the moving "latest" tag).
curl -s https://api.github.com/repos/BtbN/FFmpeg-Builds/releases   | grep -o 'https://[^"]*win64-lgpl\.zip' | head -5
```

Choose one URL from that list, then:

```bash
curl -L -o /tmp/ffmpeg.zip "<the URL you chose>"
sha256sum /tmp/ffmpeg.zip
```

Paste that exact URL and hash into the script below. LGPL (not GPL) because the binary is redistributed inside the plugin; record the chosen release tag in the commit message so the pin is traceable.

- [ ] **Step 2: Write the fetch script.** Create `streamdeck-plugin/scripts/fetch-ffmpeg.mjs`:

```js
/**
 * Download the pinned ffmpeg into the plugin bundle so the packaged plugin
 * needs no user setup. Runs on the DEV machine before `pack`, never at
 * runtime. The SHA-256 is pinned: a swapped or corrupted upstream artifact
 * fails the build instead of shipping silently.
 */
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, copyFileSync, writeFileSync, readdirSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const URL_ = "PASTE_PINNED_RELEASE_URL_HERE";
const SHA256 = "PASTE_SHA256_HERE";
const OUT_DIR = path.resolve("com.jacobchoi.jacky-control.sdPlugin/bin");

if (existsSync(path.join(OUT_DIR, "ffmpeg.exe"))) {
  console.log("ffmpeg already bundled — nothing to do");
  process.exit(0);
}

const res = await fetch(URL_);
if (!res.ok) throw new Error(`download failed: ${res.status}`);
const buf = Buffer.from(await res.arrayBuffer());

const got = createHash("sha256").update(buf).digest("hex");
if (got !== SHA256) throw new Error(`SHA-256 mismatch: expected ${SHA256}, got ${got}`);

const tmp = mkdtempSync(path.join(tmpdir(), "ffmpeg-"));
const zip = path.join(tmp, "ffmpeg.zip");
writeFileSync(zip, buf);
// PowerShell rather than a zip dependency: this runs only on the dev machine.
execFileSync("powershell", ["-NoProfile", "-Command",
  `Expand-Archive -Path '${zip}' -DestinationPath '${tmp}' -Force`]);

const root = readdirSync(tmp).find((d) => d.startsWith("ffmpeg"));
const exe = path.join(tmp, root, "bin", "ffmpeg.exe");
mkdirSync(OUT_DIR, { recursive: true });
copyFileSync(exe, path.join(OUT_DIR, "ffmpeg.exe"));
console.log("bundled ffmpeg ->", path.join(OUT_DIR, "ffmpeg.exe"));
```

Add to `package.json` scripts: `"fetch-ffmpeg": "node scripts/fetch-ffmpeg.mjs"`. Add to `streamdeck-plugin/.gitignore`:

```
com.jacobchoi.jacky-control.sdPlugin/bin/ffmpeg.exe
```

- [ ] **Step 3: Write the failing tests.** Create `streamdeck-plugin/tests/audio-capture.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { buildFfmpegArgs } from "../src/audio-capture";

describe("buildFfmpegArgs", () => {
  it("captures the named device as 16 kHz mono WAV on stdout", () => {
    const args = buildFfmpegArgs("Microphone (Yeti GX)");
    expect(args).toContain("dshow");
    expect(args).toContain("audio=Microphone (Yeti GX)");
    expect(args.join(" ")).toContain("-ar 16000");
    expect(args.join(" ")).toContain("-ac 1");
    expect(args[args.length - 1]).toBe("pipe:1");
  });

  it("falls back to the system default when no device is configured", () => {
    // ffmpeg's dshow needs a name; "default" is the documented placeholder.
    expect(buildFfmpegArgs("").join(" ")).toContain("audio=default");
    expect(buildFfmpegArgs(undefined).join(" ")).toContain("audio=default");
  });
});
```

- [ ] **Step 4: Run to verify failure.** `cd streamdeck-plugin && npm test` — FAIL, cannot resolve `../src/audio-capture`.

- [ ] **Step 5: Implement `src/ffmpeg-path.ts`:**

```ts
import { existsSync } from "node:fs";
import path from "node:path";

/** Bundled binary first so the plugin works with nothing installed; PATH is
 *  the fallback for dev machines. null means "tell the user". */
export function resolveFfmpeg(): string | null {
  const bundled = path.resolve(process.cwd(), "bin", "ffmpeg.exe");
  if (existsSync(bundled)) return bundled;
  return process.env.PATH ? "ffmpeg" : null;
}
```

- [ ] **Step 6: Implement `src/audio-capture.ts`:**

```ts
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { resolveFfmpeg } from "./ffmpeg-path";

export const MAX_RECORD_MS = 15_000;

export function buildFfmpegArgs(device: string | undefined): string[] {
  return [
    "-hide_banner", "-loglevel", "error",
    "-f", "dshow", "-i", `audio=${device || "default"}`,
    "-ac", "1", "-ar", "16000",
    "-f", "wav", "pipe:1",
  ];
}

/** Push-to-talk recorder. `onFirstBytes` fires when audio actually starts
 *  flowing — DirectShow takes ~1-1.8 s to open a device, and the key uses
 *  this to show "Listening…" so the user knows when to speak. */
export class MicRecorder {
  private proc: ChildProcessWithoutNullStreams | null = null;
  private chunks: Buffer[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;

  start(device: string | undefined, onFirstBytes: () => void): boolean {
    const bin = resolveFfmpeg();
    if (!bin) return false;
    this.chunks = [];
    let first = true;
    this.proc = spawn(bin, buildFfmpegArgs(device));
    this.proc.stdout.on("data", (c: Buffer) => {
      if (first) { first = false; onFirstBytes(); }
      this.chunks.push(c);
    });
    this.proc.on("error", () => this.kill());
    this.timer = setTimeout(() => this.requestStop(), MAX_RECORD_MS);
    return true;
  }

  private requestStop(): void {
    // "q" is ffmpeg's graceful quit: it finalizes the WAV header.
    try { this.proc?.stdin.write("q"); } catch { /* already gone */ }
  }

  private kill(): void {
    try { this.proc?.kill(); } catch { /* already gone */ }
  }

  async stop(): Promise<Buffer> {
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    const proc = this.proc;
    if (!proc) return Buffer.alloc(0);
    this.requestStop();
    await new Promise<void>((resolve) => {
      const done = () => resolve();
      proc.once("close", done);
      // Don't hang the key if ffmpeg ignores the quit.
      setTimeout(() => { this.kill(); done(); }, 2000);
    });
    this.proc = null;
    return Buffer.concat(this.chunks);
  }
}
```

- [ ] **Step 7: Client method.** In `src/api-client.ts`, add next to `summon()`:

```ts
export type VoiceResult = {
  transcript: string;
  intent: string;
  ok: boolean;
  detail: string | null;
};
```

```ts
  async voiceCommand(wav: Uint8Array): Promise<VoiceResult> {
    const res = await this.fetchFn(this.url("/control/voice"), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.s.authToken}`,
        "Content-Type": "audio/wav",
      },
      body: wav,
      signal: AbortSignal.timeout(30_000),
    });
    if (!res.ok) throw new ControlApiError(res.status);
    return (await res.json()) as VoiceResult;
  }
```

(30 s, not the usual 10 s: this request includes a transcription round-trip.)

- [ ] **Step 8: The voice action.** Create `src/actions/voice.ts`:

```ts
import {
  action,
  SingletonAction,
  type JsonValue,
  type KeyDownEvent,
  type KeyUpEvent,
  type SendToPluginEvent,
} from "@elgato/streamdeck";
import { MicRecorder } from "../audio-capture";
import { handlePiEvent } from "../pi-bridge";
import { getClient } from "../runtime";

type VoiceSettings = { inputDevice?: string };

const SHOW_RESULT_MS = 4000;

@action({ UUID: "com.jacobchoi.jacky-control.voice" })
export class Voice extends SingletonAction<VoiceSettings> {
  private recorder: MicRecorder | null = null;
  private heardAudio = false;

  override onSendToPlugin(ev: SendToPluginEvent<JsonValue, VoiceSettings>): Promise<void> {
    return handlePiEvent(ev.payload);
  }

  override async onKeyDown(ev: KeyDownEvent<VoiceSettings>): Promise<void> {
    const { inputDevice } = await ev.action.getSettings<VoiceSettings>();
    this.heardAudio = false;
    this.recorder = new MicRecorder();
    const started = this.recorder.start(inputDevice, () => {
      // Only now is the device actually delivering audio.
      this.heardAudio = true;
      void ev.action.setTitle("Listening…").catch(() => {});
    });
    if (!started) {
      this.recorder = null;
      await ev.action.setTitle("No\nffmpeg");
      await ev.action.showAlert();
    }
  }

  override async onKeyUp(ev: KeyUpEvent<VoiceSettings>): Promise<void> {
    const recorder = this.recorder;
    this.recorder = null;
    if (!recorder) return;
    const wav = await recorder.stop();
    if (!this.heardAudio || wav.length < 1000) {
      await ev.action.setTitle("Hold\nlonger");
      await ev.action.showAlert();
      this.clearLater(ev);
      return;
    }
    const client = getClient();
    if (!client) {
      await ev.action.setTitle("");
      await ev.action.showAlert();
      return;
    }
    await ev.action.setTitle("Thinking…");
    try {
      const result = await client.voiceCommand(wav);
      await ev.action.setTitle(result.detail || result.transcript);
      if (result.ok) await ev.action.showOk();
      else await ev.action.showAlert();
    } catch {
      await ev.action.setTitle("Failed");
      await ev.action.showAlert();
    }
    this.clearLater(ev);
  }

  private clearLater(ev: KeyDownEvent<VoiceSettings> | KeyUpEvent<VoiceSettings>): void {
    setTimeout(() => void ev.action.setTitle("").catch(() => {}), SHOW_RESULT_MS);
  }
}
```

- [ ] **Step 9: Device dropdown.** In `src/pi-bridge.ts`, add a case alongside `get-playlists`:

```ts
    case "get-audio-devices": {
      try {
        const { listAudioDevices } = await import("./audio-devices");
        await reply({ event: "audio-devices", data: await listAudioDevices() });
      } catch (err) {
        const error = err instanceof Error ? err.message : String(err);
        await reply({ event: "audio-devices-error", error });
      }
      break;
    }
```

Create `src/audio-devices.ts`:

```ts
import { execFile } from "node:child_process";
import { resolveFfmpeg } from "./ffmpeg-path";

/** ffmpeg prints the device list to stderr and exits non-zero by design. */
export function listAudioDevices(): Promise<string[]> {
  return new Promise((resolve) => {
    const bin = resolveFfmpeg();
    if (!bin) return resolve([]);
    execFile(
      bin,
      ["-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
      (_err, _stdout, stderr) => {
        const names = [...String(stderr).matchAll(/"([^"]+)"\s*\(audio\)/g)].map(
          (m) => m[1],
        );
        resolve([...new Set(names)]);
      },
    );
  });
}
```

In `ui/settings.html`, add a voice section after the playlist one:

```html
  <!-- Voice key only: which microphone to record from. -->
  <div id="voice-settings" style="display: none">
    <sdpi-item label="Microphone">
      <sdpi-select id="mic-select" setting="inputDevice" placeholder="System default"></sdpi-select>
    </sdpi-item>
    <div id="voice-error" style="display: none; padding-left: 4px; font-size: 9pt; color: #e94560"></div>
  </div>
```

In the inline `<script>`, add element handles beside the existing ones:

```js
    const voiceDiv = document.getElementById("voice-settings");
    const micSelect = document.getElementById("mic-select");
    const voiceError = document.getElementById("voice-error");
    let isVoice = false;
```

Add two branches to the existing `sendToPropertyInspector.subscribe` if/else-if
chain (alongside the `playlists` ones — they must be part of the SAME chain,
not a second subscribe):

```js
      } else if (p.event === "audio-devices") {
        voiceError.style.display = "none";
        setOptions(micSelect, (p.data || []).map((d) => ({ value: d, label: d })));
      } else if (p.event === "audio-devices-error") {
        voiceError.textContent = "Could not list microphones: " + (p.error || "unknown error");
        voiceError.style.display = "block";
```

And a boot branch in the IIFE, mirroring the summon/playlist ones. Note it does
NOT depend on sign-in — the device list comes from the local ffmpeg, not the
bot — so unlike `get-channels` it is not re-requested on `auth-status`:

```js
      isVoice = actionInfo.action.endsWith(".voice");
      if (isVoice) {
        voiceDiv.style.display = "";
        sendToPlugin({ event: "get-audio-devices" });
      }
```

- [ ] **Step 10: Register and manifest.** In `src/plugin.ts` import `Voice` and add `streamDeck.actions.registerAction(new Voice());`. Create `imgs/voice.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><rect width="72" height="72" rx="14" fill="#1a1a2e"/><rect x="29" y="14" width="14" height="26" rx="7" fill="#e94560"/><path d="M22 34 a14 14 0 0 0 28 0" fill="none" stroke="#e94560" stroke-width="4" stroke-linecap="round"/><rect x="34" y="48" width="4" height="8" rx="2" fill="#e94560"/><rect x="26" y="57" width="20" height="4" rx="2" fill="#e94560"/></svg>
```

In `manifest.json`, bump `"Version"` to `"0.4.0.0"` and append (matching the file's compact style):

```json
    {
      "UUID": "com.jacobchoi.jacky-control.voice",
      "Name": "Voice Command",
      "Icon": "imgs/voice",
      "Tooltip": "Hold to speak a command",
      "PropertyInspectorPath": "ui/settings.html",
      "Controllers": ["Keypad"],
      "States": [{ "Image": "imgs/voice", "TitleAlignment": "bottom" }]
    }
```

- [ ] **Step 11: Verify.** `npm test` (expect 35), `npm run build`, `npx tsc --noEmit`, and `npx @elgato/cli@latest validate com.jacobchoi.jacky-control.sdPlugin` (0 errors; the 2 known cosmetic warnings are fine). Then `npm run fetch-ffmpeg` and confirm `…sdPlugin/bin/ffmpeg.exe` exists and `git status` shows it ignored.

- [ ] **Step 12: Commit**

```bash
git add streamdeck-plugin/
git commit -m "feat(deck): voice command key with bundled ffmpeg, v0.4.0.0"
```

### Task 11: Now Playing polish (independent of voice)

**Files:** `streamdeck-plugin/src/image.ts`, `streamdeck-plugin/src/actions/now-playing.ts`, `streamdeck-plugin/tests/image.test.ts`

- [ ] **Step 1: Write the failing test.** Create `streamdeck-plugin/tests/image.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { letterboxSvg } from "../src/image";

describe("letterboxSvg", () => {
  it("preserves the aspect ratio of 16:9 artwork on a square key", () => {
    const svg = letterboxSvg("data:image/jpeg;base64,AAA");
    // "meet" scales to fit INSIDE the square instead of stretching to fill.
    expect(svg).toContain('preserveAspectRatio="xMidYMid meet"');
    expect(svg).toContain("data:image/jpeg;base64,AAA");
    expect(svg).toMatch(/^<svg /);
  });

  it("fills the letterbox bars rather than leaving them transparent", () => {
    expect(letterboxSvg("data:image/jpeg;base64,AAA")).toContain("<rect");
  });
});
```

- [ ] **Step 2: Run to verify failure.** `npm test` — cannot resolve `../src/image`.

- [ ] **Step 3: Implement `src/image.ts`:**

```ts
/** Wrap artwork in a square SVG so a 16:9 thumbnail is letterboxed instead of
 *  stretched. setImage accepts an SVG string, so this needs no image library
 *  and no native dependency. */
const SIZE = 144; // Stream Deck @2x key
const BG = "#1a1a2e";

export function letterboxSvg(dataUri: string): string {
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${SIZE}" height="${SIZE}" ` +
    `viewBox="0 0 ${SIZE} ${SIZE}">` +
    `<rect width="${SIZE}" height="${SIZE}" fill="${BG}"/>` +
    `<image href="${dataUri}" width="${SIZE}" height="${SIZE}" ` +
    `preserveAspectRatio="xMidYMid meet"/>` +
    `</svg>`
  );
}
```

- [ ] **Step 4: Use it, and add the scroll timer.** In `src/actions/now-playing.ts`:

Add the import `import { letterboxSvg } from "../image";` and change the artwork apply line from `a.setImage(uri)` to:

```ts
            for (const a of this.actions) void a.setImage(letterboxSvg(uri)).catch(() => {});
```

Then make the title scroll on its own clock rather than the 5 s poll. Add fields:

```ts
  private scrollTimer: ReturnType<typeof setInterval> | null = null;
  private titleSuffix = "";
```

Replace the `else` branch that builds a scrolling title with one that records state and lets the timer render:

```ts
    else {
      if (s.data.title !== this.lastTitle) {
        this.offset = 0;
        this.lastTitle = s.data.title;
      }
      this.titleSuffix = s.data.paused ? "\n⏸" : "";
      this.startScrolling(s.data.title);
      return; // the scroll timer renders this key's title
    }
```

and add the two helpers plus a render method:

```ts
  private renderTitle(): void {
    if (this.lastTitle === null) return;
    const text = marquee(this.lastTitle, this.offset, TITLE_WIDTH) + this.titleSuffix;
    for (const a of this.actions) a.setTitle(text).catch(() => {});
  }

  private startScrolling(title: string): void {
    this.renderTitle();
    // Scroll on a 400 ms clock, not the 5 s poll — otherwise a long title
    // crawls one step per poll and never reads as scrolling.
    if (title.length <= TITLE_WIDTH) {
      this.stopScrolling();
      return;
    }
    if (this.scrollTimer) return;
    this.scrollTimer = setInterval(() => {
      this.offset += 1;
      this.renderTitle();
    }, 400);
  }

  private stopScrolling(): void {
    if (this.scrollTimer) {
      clearInterval(this.scrollTimer);
      this.scrollTimer = null;
    }
  }
```

Every non-scrolling branch (`unconfigured`, `unauthorized`, `offline`, `!active`, no title) must call `this.stopScrolling(); this.lastTitle = null;` before setting its static text, so a stale timer cannot overwrite it. Call `this.stopScrolling()` in `onWillDisappear`'s zero-visible branch too.

- [ ] **Step 5: Verify.** `npm test` (expect 37), `npm run build`, `npx tsc --noEmit`.

- [ ] **Step 6: Commit**

```bash
git add streamdeck-plugin/src/image.ts streamdeck-plugin/src/actions/now-playing.ts streamdeck-plugin/tests/image.test.ts
git commit -m "feat(deck): smooth title scroll and correct artwork ratio"
```

---

## Part 4 — Ship

### Task 12: Docs, deploy, live verification, pack

- [ ] **Step 1: Runbook.** In `docs/streamdeck-control.md`, add to the behavior notes:

```markdown
- **Voice Command** key: hold it, speak, release. Wait for "Listening…" before
  speaking — opening the mic takes about a second. Say `skip`, `pause`,
  `resume`, `louder`, `quieter`, `play playlist <name>`, `add playlist <name>`,
  or just say a song ("play bohemian rhapsody", or simply "bohemian rhapsody").
  `stop` is deliberately NOT a voice command — use the Stop key. Recording caps
  at 15 seconds. The microphone is chosen per key in its settings, and is only
  open while the key is held.
- Voice commands appear in the dashboard's Command History with a Voice badge,
  showing both what was heard and the action it ran.
```

- [ ] **Step 2: Merge and deploy the bot.**

```bash
git checkout master && git merge --no-ff feat/streamdeck-voice -m "Merge feat/streamdeck-voice: voice command key" && git push origin master
```

Then add `OPENAI_API_KEY` to the VM's `deploy/.env` (copy the value from the local `deploy/.env`) and redeploy the bot only:

```bash
gcloud compute ssh personal-project-machine --project=personal-server-492701 --zone=us-east1-b --command="cd ~/discord-music-bot && sudo git -c safe.directory=\$PWD pull origin master && sudo docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build bot"
```

- [ ] **Step 3: Verify the route is live.**

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST "https://control.jacky-music-bot.com/control/voice"
```

Expected: `401` (auth is enforced before anything else).

- [ ] **Step 4: Pack.**

```bash
cd streamdeck-plugin && npm run fetch-ffmpeg && npm run build \
  && rm -f com.jacobchoi.jacky-control.streamDeckPlugin \
  && npx @elgato/cli pack com.jacobchoi.jacky-control.sdPlugin --force
```

Confirm the `.streamDeckPlugin` is roughly 30–40 MB (it now contains ffmpeg) and deliver it. Afterwards run `git checkout -- streamdeck-plugin/com.jacobchoi.jacky-control.sdPlugin/manifest.json` — `pack` reformats it.

- [ ] **Step 5: User walkthrough.** Install; drop a Voice Command key; pick the microphone in its settings. Then, with a live session: each grammar row spoken aloud; a hold too short to register; speaking with no session (⚠); a playlist by voice; an unknown playlist name; a long track title scrolling smoothly; a 16:9 thumbnail rendering undistorted; and a voice entry appearing in the dashboard's Command History with both the transcript and the action.
