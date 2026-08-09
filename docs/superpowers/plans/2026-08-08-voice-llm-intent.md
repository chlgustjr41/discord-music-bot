# Voice LLM Intent Interpretation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Interpret voice commands with an LLM instead of a fixed grammar — so "play X" interrupts and plays, "play X next" jumps the queue, "add X" appends, several instructions in one breath all run, and dashboard controls like shuffle and clear-queue become reachable by voice.

**Architecture:** Only the intent stage changes. Transcript → `gpt-4o-mini` with structured outputs → a validated list of actions from a closed vocabulary → dispatched in order. The existing deterministic parser stays as an offline fallback. Capture, transcription, auth, and logging are untouched.

**Tech Stack:** unchanged — Python 3.11 / aiohttp / pytest; TypeScript / vitest.

**Spec:** `docs/superpowers/specs/2026-08-08-voice-llm-intent-design.md` — read it first; it governs.

**House rules (every task):** TDD — write the test, watch it fail for the right reason, implement, watch it pass. Bot gates: `cd services/bot && py -m pytest -q` and `uvx ruff@0.15.20 check src tests`. Plugin gates: `cd streamdeck-plugin && npm test && npm run build && npx tsc --noEmit`. Commit per task with the given message. Use the `py` launcher. Never run `npx @elgato/cli pack` mid-plan — it reformats `manifest.json`.

**Baselines:** bot 198 tests; plugin 52 tests; branch `feat/voice-llm-intent` off master.

**Security note that shapes the design:** the model's output is untrusted input. `validate_actions` is the boundary and lives in its own pure module, separate from anything that touches the network, so it can be tested exhaustively. The vocabulary has no deletion verb — that is the guarantee, not the prompt.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/jacky/api/voice_actions.py` | create | `Action`, the JSON schema, `validate_actions` — the security boundary, pure |
| `src/jacky/api/voice_llm.py` | create | `LlmIntentInterpreter`, injectable HTTP |
| `src/jacky/api/voice_intent.py` | modify | fallback parser, new placement semantics |
| `src/jacky/voice_control.py` | modify | `dispatch_all` + new actions/placements |
| `src/jacky/api/control.py` | modify | LLM → fallback → validate → dispatch_all → per-action logging |
| `src/jacky/config.py` | modify | `openai_intent_model` |
| `src/jacky/core/bot.py` | modify | construct the interpreter |
| `tests/conftest.py` | modify | `FakeRepo.clear_queue` |
| `tests/test_voice_actions.py` | create | validation boundary |
| `tests/test_voice_llm.py` | create | interpreter contract |
| `tests/test_voice_control.py`, `tests/test_control_api.py`, `tests/test_voice_intent.py` | modify | new behavior |
| `deploy/docker-compose.yml`, `deploy/.env.example` | modify | `OPENAI_INTENT_MODEL` |
| `streamdeck-plugin/src/api-client.ts` | modify | `VoiceResult.actions` |

---

## Task 1: The action vocabulary and validator

**Files:** `services/bot/src/jacky/api/voice_actions.py`, `services/bot/tests/test_voice_actions.py`

- [ ] **Step 1: Write the failing tests.** Create `services/bot/tests/test_voice_actions.py`:

```python
"""The security boundary: model output is untrusted and re-validated here."""

from jacky.api.voice_actions import ACTION_SCHEMA, Action, validate_actions


def test_valid_actions_pass_through():
    got = validate_actions([
        {"action": "play", "query": "bohemian rhapsody", "placement": "now"},
        {"action": "skip", "count": 2},
    ])
    assert got == [
        Action("play", query="bohemian rhapsody", placement="now"),
        Action("skip", count=2),
    ]


def test_unknown_verbs_are_dropped_not_executed():
    """A confused or adversarial model must not be able to invent verbs."""
    got = validate_actions([
        {"action": "delete_playlist", "name": "chill"},
        {"action": "drop_database"},
        {"action": "skip"},
    ])
    assert got == [Action("skip", count=1)]


def test_deletion_is_unreachable_by_any_shape():
    for raw in (
        {"action": "delete"},
        {"action": "remove_playlist", "name": "x"},
        {"action": "clear", "target": "playlists"},
        {"action": "stop"},
    ):
        assert validate_actions([raw]) == []


def test_placement_defaults_to_now_and_rejects_junk():
    assert validate_actions([{"action": "play", "query": "x"}])[0].placement == "now"
    assert validate_actions(
        [{"action": "play", "query": "x", "placement": "sideways"}]
    )[0].placement == "now"


def test_numeric_fields_are_clamped():
    assert validate_actions([{"action": "skip", "count": 999}])[0].count == 10
    assert validate_actions([{"action": "skip", "count": 0}])[0].count == 1
    assert validate_actions([{"action": "volume", "level": 500}])[0].level == 100
    assert validate_actions([{"action": "volume", "level": -20}])[0].level == 0


def test_more_than_five_actions_are_truncated():
    raw = [{"action": "skip"} for _ in range(9)]
    assert len(validate_actions(raw)) == 5


def test_malformed_entries_do_not_kill_the_batch():
    got = validate_actions(["nonsense", None, 42, {"no_action_key": 1},
                            {"action": "pause"}])
    assert got == [Action("pause")]


def test_actions_requiring_text_are_dropped_when_it_is_missing():
    assert validate_actions([{"action": "play"}]) == []
    assert validate_actions([{"action": "playlist", "name": "  "}]) == []


def test_non_list_input_is_empty():
    assert validate_actions(None) == []
    assert validate_actions({"action": "skip"}) == []


def test_schema_declares_the_closed_vocabulary():
    """The schema is what constrains the model at decode time; if a verb is
    missing here the model cannot emit it at all."""
    verbs = ACTION_SCHEMA["properties"]["actions"]["items"]["properties"]["action"]["enum"]
    assert set(verbs) == {
        "play", "playlist", "skip", "pause", "resume",
        "volume", "shuffle", "clear_queue", "loop",
    }
    assert not any("delete" in v or "remove" in v for v in verbs)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/bot && py -m pytest tests/test_voice_actions.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'jacky.api.voice_actions'`.

- [ ] **Step 3: Implement.** Create `services/bot/src/jacky/api/voice_actions.py`:

```python
"""Closed action vocabulary for voice commands, plus the validator.

This is the security boundary. The model's output is UNTRUSTED input: the
JSON schema constrains it at decode time, but that guarantee is re-checked
here regardless. A schema the model should obey is not the same as one it did.

There is deliberately no verb for deleting a playlist, history, or a session.
That is the guarantee — not a prompt instruction the model might ignore.
`clear_queue` is the only destructive action and empties only the queue.
"""

from dataclasses import dataclass

MAX_ACTIONS = 5
MAX_SKIP = 10
PLACEMENTS = ("now", "next", "end")
LOOP_MODES = ("off", "track", "queue")

_VERBS = (
    "play", "playlist", "skip", "pause", "resume",
    "volume", "shuffle", "clear_queue", "loop",
)
_NEEDS_QUERY = ("play",)
_NEEDS_NAME = ("playlist",)


@dataclass(frozen=True)
class Action:
    action: str
    query: str = ""
    name: str = ""
    placement: str = "now"
    count: int = 1
    level: int | None = None
    delta: int | None = None
    mode: str = "off"


ACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["actions"],
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action"],
                "properties": {
                    "action": {"type": "string", "enum": list(_VERBS)},
                    "query": {"type": "string"},
                    "name": {"type": "string"},
                    "placement": {"type": "string", "enum": list(PLACEMENTS)},
                    "count": {"type": "integer"},
                    "level": {"type": "integer"},
                    "delta": {"type": "integer"},
                    "mode": {"type": "string", "enum": list(LOOP_MODES)},
                },
            },
        }
    },
}


def _clamp(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def validate_actions(raw) -> list[Action]:
    """Drop anything not in the vocabulary; clamp every number. Never raises."""
    if not isinstance(raw, list):
        return []
    out: list[Action] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        verb = entry.get("action")
        if verb not in _VERBS:
            continue
        query = str(entry.get("query") or "").strip()
        name = str(entry.get("name") or "").strip()
        if verb in _NEEDS_QUERY and not query:
            continue
        if verb in _NEEDS_NAME and not name:
            continue
        placement = entry.get("placement")
        mode = entry.get("mode")
        level = entry.get("level")
        delta = entry.get("delta")
        out.append(Action(
            action=verb,
            query=query,
            name=name,
            placement=placement if placement in PLACEMENTS else "now",
            count=_clamp(entry.get("count", 1), 1, MAX_SKIP, 1),
            level=None if level is None else _clamp(level, 0, 100, 80),
            delta=None if delta is None else _clamp(delta, -100, 100, 0),
            mode=mode if mode in LOOP_MODES else "off",
        ))
        if len(out) == MAX_ACTIONS:
            break
    return out
```

- [ ] **Step 4: Verify.** `py -m pytest -q` (expect 208) and `uvx ruff@0.15.20 check src tests`.

- [ ] **Step 5: Commit**

```bash
git add services/bot/src/jacky/api/voice_actions.py services/bot/tests/test_voice_actions.py
git commit -m "feat(voice): closed action vocabulary with untrusted-output validation"
```

## Task 2: The LLM interpreter

**Files:** `services/bot/src/jacky/api/voice_llm.py`, `services/bot/tests/test_voice_llm.py`

- [ ] **Step 1: Write the failing tests.** Create `services/bot/tests/test_voice_llm.py`:

```python
"""Interpreter contract. The HTTP client is injected; tests never hit OpenAI."""

import json

import pytest

from jacky.api.voice_actions import Action


class _Resp:
    def __init__(self, status, payload):
        self.status, self._payload = status, payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Http:
    def __init__(self, resp):
        self._resp, self.calls = resp, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._resp


def _completion(actions):
    return {"choices": [{"message": {"content": json.dumps({"actions": actions})}}]}


async def test_interprets_a_multi_command_utterance_in_order():
    from jacky.api.voice_llm import LlmIntentInterpreter

    http = _Http(_Resp(200, _completion([
        {"action": "skip"},
        {"action": "play", "query": "creep", "placement": "end"},
    ])))
    got = await LlmIntentInterpreter(http, "sk", "gpt-4o-mini").interpret("skip then add creep")
    assert got == [Action("skip", count=1), Action("play", query="creep", placement="end")]


async def test_request_constrains_the_model_with_the_strict_schema():
    from jacky.api.voice_actions import ACTION_SCHEMA
    from jacky.api.voice_llm import LlmIntentInterpreter

    http = _Http(_Resp(200, _completion([{"action": "pause"}])))
    await LlmIntentInterpreter(http, "sk-test", "gpt-4o-mini").interpret("pause")
    _url, kwargs = http.calls[0]
    body = kwargs["json"]
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == ACTION_SCHEMA


async def test_model_output_still_goes_through_validation():
    """Even inside a 200 response, an unknown verb must not survive."""
    from jacky.api.voice_llm import LlmIntentInterpreter

    http = _Http(_Resp(200, _completion([
        {"action": "delete_playlist", "name": "chill"},
        {"action": "pause"},
    ])))
    got = await LlmIntentInterpreter(http, "sk", "m").interpret("whatever")
    assert got == [Action("pause")]


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": [{"message": {"content": "not json"}}]},
        {"choices": []},
        {},
        {"choices": [{"message": {"content": json.dumps({"actions": []})}}]},
    ],
)
async def test_unusable_responses_raise_interpret_error(payload):
    from jacky.api.voice_llm import InterpretError, LlmIntentInterpreter

    with pytest.raises(InterpretError):
        await LlmIntentInterpreter(_Http(_Resp(200, payload)), "sk", "m").interpret("x")


async def test_non_200_raises_interpret_error():
    from jacky.api.voice_llm import InterpretError, LlmIntentInterpreter

    with pytest.raises(InterpretError):
        await LlmIntentInterpreter(_Http(_Resp(500, {})), "sk", "m").interpret("x")


async def test_network_fault_is_wrapped():
    from jacky.api.voice_llm import InterpretError, LlmIntentInterpreter

    class _Boom:
        def post(self, *a, **k):
            raise OSError("connection reset")

    with pytest.raises(InterpretError) as exc:
        await LlmIntentInterpreter(_Boom(), "sk", "m").interpret("x")
    assert isinstance(exc.value.__cause__, OSError)


async def test_interpreter_never_logs_or_echoes_the_transcript_in_errors():
    """Transcripts must not reach stdout; an exception message would."""
    from jacky.api.voice_llm import InterpretError, LlmIntentInterpreter

    secret = "play my extremely private playlist name"
    with pytest.raises(InterpretError) as exc:
        await LlmIntentInterpreter(_Http(_Resp(500, {})), "sk", "m").interpret(secret)
    assert secret not in str(exc.value)
```

- [ ] **Step 2: Run to verify failure**

Run: `py -m pytest tests/test_voice_llm.py -q`
Expected: FAIL — no module `jacky.api.voice_llm`.

- [ ] **Step 3: Implement.** Create `services/bot/src/jacky/api/voice_llm.py`:

```python
"""Turn a transcript into a list of actions using an LLM.

Structured outputs constrain the model at decode time, but the result is still
run through validate_actions — the model is untrusted input. The system prompt
carries only the rules the schema cannot express.
"""

import json
from typing import Any

from jacky.api.voice_actions import ACTION_SCHEMA, MAX_ACTIONS, Action, validate_actions

URL = "https://api.openai.com/v1/chat/completions"

SYSTEM_PROMPT = (
    "You convert a spoken music command into actions for a Discord music bot. "
    "Rules:\n"
    "- Extract search terms LITERALLY from the user's words. Never invent a "
    "song, artist, or playlist the user did not say.\n"
    "- 'play X' means play X immediately (placement 'now'). 'play X next' "
    "means placement 'next'. 'add X' or 'queue X' means placement 'end'.\n"
    "- Stopping, halting or 'turn it off' means pause. There is no way to end "
    "the session or delete anything; never try.\n"
    "- 'clear' refers only to clearing the queue.\n"
    f"- Emit at most {MAX_ACTIONS} actions, in the order the user said them.\n"
    "- If the user simply names music with no verb, treat it as play 'now'."
)


class InterpretError(Exception):
    pass


class LlmIntentInterpreter:
    def __init__(self, http: Any, api_key: str, model: str) -> None:
        self.http, self.api_key, self.model = http, api_key, model

    async def interpret(self, transcript: str) -> list[Action]:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": transcript},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "voice_actions",
                    "strict": True,
                    "schema": ACTION_SCHEMA,
                },
            },
        }
        try:
            async with self.http.post(
                URL, json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
            ) as resp:
                if resp.status != 200:
                    # Status only — never the transcript (see spec §Security).
                    raise InterpretError(f"interpretation failed: {resp.status}")
                payload = await resp.json()
        except InterpretError:
            raise
        except Exception as exc:  # noqa: BLE001 — one failure mode for the caller
            raise InterpretError(f"interpretation request failed: {exc}") from exc

        try:
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise InterpretError("unusable interpretation response") from exc

        actions = validate_actions(parsed.get("actions"))
        if not actions:
            raise InterpretError("no usable actions")
        return actions
```

- [ ] **Step 4: Verify.** `py -m pytest -q` (expect 218) and ruff.

- [ ] **Step 5: Commit**

```bash
git add services/bot/src/jacky/api/voice_llm.py services/bot/tests/test_voice_llm.py
git commit -m "feat(voice): llm intent interpreter with structured outputs"
```

## Task 3: Dispatcher — new actions and placements

**Files:** `services/bot/src/jacky/voice_control.py`, `services/bot/tests/conftest.py`, `services/bot/tests/test_voice_control.py`

- [ ] **Step 1: Add `clear_queue` to FakeRepo.** In `services/bot/tests/conftest.py`, next to `shuffle_queue`:

```python
    async def clear_queue(self, sid):
        self.states.setdefault(sid, {})["queue"] = []
```

- [ ] **Step 2: Write the failing tests.** Append to `services/bot/tests/test_voice_control.py`:

```python
# ── action dispatch (LLM vocabulary) ─────────────────────────────────────

from jacky.api.voice_actions import Action


async def test_play_now_replaces_the_current_track(dispatcher, service, guild_id, sid):
    """'play X' interrupts. The interrupted track is dropped, not requeued —
    the user said skip-and-play."""
    await service.repo.update_state(sid, {"currentTrack": {"title": "Old"}})
    result = (await dispatcher.dispatch_all(guild_id, [
        Action("play", query="a song", placement="now")
    ]))[0]
    assert result.ok
    state = await service.repo.get_state(sid)
    assert state["currentTrack"]["title"] == "Song"
    assert state["queue"] == []


async def test_play_next_jumps_the_queue_without_interrupting(
    dispatcher, service, guild_id, sid
):
    await service.repo.update_state(
        sid, {"currentTrack": {"title": "Now"}, "queue": [{"title": "Old"}]}
    )
    before = len(service.node.updates)
    result = (await dispatcher.dispatch_all(guild_id, [
        Action("play", query="a song", placement="next")
    ]))[0]
    assert result.ok
    queue = (await service.repo.get_state(sid))["queue"]
    assert [t["title"] for t in queue] == ["Song", "Old"]
    assert len(service.node.updates) == before, "must not interrupt"


async def test_play_end_appends(dispatcher, service, guild_id, sid):
    await service.repo.update_state(
        sid, {"currentTrack": {"title": "Now"}, "queue": [{"title": "Old"}]}
    )
    await dispatcher.dispatch_all(guild_id, [
        Action("play", query="a song", placement="end")
    ])
    queue = (await service.repo.get_state(sid))["queue"]
    assert [t["title"] for t in queue] == ["Old", "Song"]


async def test_skip_count_pops_exactly_and_skips_once(
    dispatcher, service, guild_id, sid
):
    """Repeated skip() would race the TrackEnd auto-advance and drop an
    unpredictable number; popping count-1 first makes it exact."""
    await service.repo.update_state(sid, {
        "currentTrack": {"title": "Now"},
        "queue": [{"title": "A"}, {"title": "B"}, {"title": "C"}],
    })
    before = len(service.node.updates)
    result = (await dispatcher.dispatch_all(guild_id, [Action("skip", count=3)]))[0]
    assert result.ok
    queue = (await service.repo.get_state(sid))["queue"]
    assert [t["title"] for t in queue] == ["C"]
    assert len(service.node.updates) == before + 1, "exactly one skip issued"


async def test_shuffle_and_clear_queue(dispatcher, service, guild_id, sid):
    await service.repo.update_state(sid, {"queue": [{"title": "A"}, {"title": "B"}]})
    assert (await dispatcher.dispatch_all(guild_id, [Action("shuffle")]))[0].ok

    assert (await dispatcher.dispatch_all(guild_id, [Action("clear_queue")]))[0].ok
    assert (await service.repo.get_state(sid))["queue"] == []


async def test_absolute_volume_and_loop(dispatcher, service, guild_id, sid):
    result = (await dispatcher.dispatch_all(guild_id, [
        Action("volume", level=42)
    ]))[0]
    assert result.ok
    assert (await service.repo.get_state(sid))["volume"] == 42
    assert result.log_arg == "42"

    await dispatcher.dispatch_all(guild_id, [Action("loop", mode="queue")])
    assert (await service.repo.get_state(sid))["loopMode"] == "queue"


async def test_actions_run_in_order_and_a_failure_does_not_block_the_rest(
    dispatcher, service, guild_id, sid
):
    from jacky.audio.models import LoadResult

    service.node.load_results["ytsearch:nothing"] = LoadResult(kind="empty", tracks=[])
    await service.repo.update_state(sid, {"currentTrack": {"title": "Now"}})
    results = await dispatcher.dispatch_all(guild_id, [
        Action("play", query="nothing", placement="end"),
        Action("pause"),
    ])
    assert [r.ok for r in results] == [False, True]
    assert (await service.repo.get_state(sid))["isPaused"] is True


async def test_a_raising_action_is_contained(dispatcher, service, guild_id):
    """One exploding action must not abort the batch."""
    async def boom(*_a, **_k):
        raise RuntimeError("firestore down")

    service.repo.shuffle_queue = boom   # the dispatcher calls the repo, not the service
    results = await dispatcher.dispatch_all(guild_id, [
        Action("shuffle"), Action("pause"),
    ])
    assert results[0].ok is False
    assert results[1].ok is True
```

- [ ] **Step 3: Run to verify failure**

Run: `py -m pytest tests/test_voice_control.py -q`
Expected: FAIL — `AttributeError: 'VoiceIntentDispatcher' object has no attribute 'dispatch_all'`.

- [ ] **Step 4: Implement.** In `services/bot/src/jacky/voice_control.py`, add the import and the new methods. Leave the existing `dispatch(intent)`, `_search` and `_playlist` in place FOR NOW so the suite keeps passing — Task 4 switches the fallback parser to `Action`s, which makes them dead code, and deletes them there.

```python
from jacky.api.voice_actions import Action
```

```python
    async def dispatch_all(
        self, guild_id: int, actions: list[Action]
    ) -> list[DispatchResult]:
        """Run actions in order. One failure never blocks the rest — the user
        asked for several things and should get the ones that work."""
        results: list[DispatchResult] = []
        for action in actions:
            try:
                results.append(await self._dispatch_action(guild_id, action))
            except Exception:  # noqa: BLE001 — contained per action
                # No transcript in the message: `action` may carry the spoken
                # query and this reaches container stdout.
                log.exception("voice action failed: %s", action.action)
                results.append(DispatchResult(False, f"{action.action} failed"))
        return results

    async def _dispatch_action(self, guild_id: int, action: Action) -> DispatchResult:
        sid = str(guild_id)
        kind = action.action
        if kind == "pause":
            await self.service.pause(guild_id, True)
            return DispatchResult(True, "Paused")
        if kind == "resume":
            await self.service.pause(guild_id, False)
            return DispatchResult(True, "Resumed")
        if kind == "skip":
            return await self._skip(guild_id, sid, action.count)
        if kind == "volume":
            return await self._volume(guild_id, sid, action)
        if kind == "shuffle":
            count = await self.repo.shuffle_queue(sid)
            return DispatchResult(True, f"Shuffled {count}")
        if kind == "clear_queue":
            await self.repo.clear_queue(sid)
            return DispatchResult(True, "Queue cleared")
        if kind == "loop":
            await self.repo.update_state(sid, {"loopMode": action.mode})
            return DispatchResult(True, f"Loop {action.mode}")
        if kind == "play":
            return await self._play(guild_id, sid, action)
        if kind == "playlist":
            return await self._playlist_action(guild_id, sid, action)
        return DispatchResult(False, "Unknown command")

    async def _skip(self, guild_id: int, sid: str, count: int) -> DispatchResult:
        # Pop count-1 first, then skip once. Calling skip() repeatedly would
        # race the TrackEnd-driven auto-advance and drop an unpredictable
        # number of tracks.
        if count > 1:
            queue = await self.repo.get_queue(sid)
            await self.repo.update_state(sid, {"queue": queue[count - 1:]})
        await self.service.skip(guild_id)
        return DispatchResult(True, "Skipped" if count == 1 else f"Skipped {count}")

    async def _volume(self, guild_id: int, sid: str, action: Action) -> DispatchResult:
        if action.level is not None:
            new = await self.service.set_volume(guild_id, action.level)
        else:
            state = await self.repo.get_state(sid) or {}
            current = state.get("volume")
            current = 80 if current is None else int(current)
            new = await self.service.set_volume(
                guild_id, current + (action.delta or VOLUME_STEP)
            )
        return DispatchResult(True, f"Volume {new}", log_arg=str(new))

    async def _play(self, guild_id: int, sid: str, action: Action) -> DispatchResult:
        try:
            result = await self.service.resolve(action.query)
        except Exception as exc:  # noqa: BLE001 — surfaced on the key
            log.warning("voice search failed: %s", exc)
            return DispatchResult(False, "Search failed")
        if not result.tracks:
            return DispatchResult(False, "No results")
        td = to_track_data(result.first, "voice command")

        if action.placement == "now":
            # Replaces playback outright. Lavalink reports the TrackEnd reason
            # as "replaced", which on_track_end ignores, so there is no
            # competing auto-advance and the old track is simply dropped.
            ok = await self.service.start_current_track(guild_id, result.first, td)
            return DispatchResult(bool(ok), td["title"] if ok else "Playback failed",
                                  log_arg=action.query)

        existing = await self.repo.get_queue(sid)
        # Decide before the write: the queue write wakes the Firestore
        # listener, which auto-starts playback when it sees the queue grow
        # while idle. No await may sit between the write and the start call.
        playing = bool((await self.repo.get_state(sid) or {}).get("currentTrack"))
        queue = [td, *existing] if action.placement == "next" else [*existing, td]
        await self.repo.update_state(sid, {"queue": queue})
        if not playing:
            await self.service.play_next(guild_id)
        return DispatchResult(True, td["title"], log_arg=action.query)

    async def _playlist_action(
        self, guild_id: int, sid: str, action: Action
    ) -> DispatchResult:
        wanted = normalize_playlist_name(action.name)
        saved = await self.repo.list_playlists(sid)
        match = next(
            (p for p in saved if normalize_playlist_name(p.get("name", "")) == wanted),
            None,
        )
        tracks = (match or {}).get("tracks") or []
        if not tracks:
            return DispatchResult(False, f"No playlist called {action.name}")
        queued = [{**t, "requestedBy": "voice command"} for t in tracks]
        existing = await self.repo.get_queue(sid)
        playing = bool((await self.repo.get_state(sid) or {}).get("currentTrack"))
        front = action.placement in ("now", "next")
        await self.repo.update_state(
            sid, {"queue": [*queued, *existing] if front else [*existing, *queued]}
        )
        if action.placement == "now" and playing:
            await self.service.skip(guild_id)
        elif not playing:
            await self.service.play_next(guild_id)
        name = match.get("name", action.name)
        return DispatchResult(True, f"{name} ({len(queued)})", log_arg=name)
```

- [ ] **Step 5: Verify.** `py -m pytest -q` (expect 226) and ruff.

- [ ] **Step 6: Commit**

```bash
git add services/bot/src/jacky/voice_control.py services/bot/tests/conftest.py services/bot/tests/test_voice_control.py
git commit -m "feat(voice): placement-aware play, shuffle, clear, loop, absolute volume"
```

## Task 4: Fallback parser to the new semantics

**Files:** `services/bot/src/jacky/api/voice_intent.py`, `services/bot/tests/test_voice_intent.py`

The parser is now the offline path. It must return `Action`s so both paths agree.

- [ ] **Step 1: Rewrite the tests' expectations.** In `services/bot/tests/test_voice_intent.py`, replace the `Intent`-based assertions with `Action`-based ones. The exact-command tests become:

```python
@pytest.mark.parametrize(
    ("said", "action"),
    [
        ("skip", Action("skip", count=1)),
        ("Skip.", Action("skip", count=1)),
        ("next", Action("skip", count=1)),
        ("pause", Action("pause")),
        ("stop", Action("pause")),          # stop-like speech pauses
        ("resume", Action("resume")),
        ("louder", Action("volume", delta=10)),
        ("quieter", Action("volume", delta=-10)),
        ("shuffle", Action("shuffle")),
        ("clear the queue", Action("clear_queue")),
    ],
)
def test_exact_commands(said, action):
    assert parse_fallback(said) == [action]


@pytest.mark.parametrize(
    ("said", "query", "placement"),
    [
        ("play bohemian rhapsody", "bohemian rhapsody", "now"),
        ("play bohemian rhapsody next", "bohemian rhapsody", "next"),
        ("add bohemian rhapsody", "bohemian rhapsody", "end"),
        ("queue bohemian rhapsody", "bohemian rhapsody", "end"),
        ("bohemian rhapsody", "bohemian rhapsody", "now"),
    ],
)
def test_play_placements(said, query, placement):
    assert parse_fallback(said) == [
        Action("play", query=query, placement=placement)
    ]


def test_playlist_placements():
    assert parse_fallback("play playlist chill") == [
        Action("playlist", name="chill", placement="now")
    ]
    assert parse_fallback("add playlist chill") == [
        Action("playlist", name="chill", placement="end")
    ]


def test_empty_transcript_is_empty_list():
    assert parse_fallback("") == []
    assert parse_fallback("...") == []


def test_query_keeps_original_punctuation_and_case():
    assert parse_fallback("play AC/DC Back in Black") == [
        Action("play", query="AC/DC Back in Black", placement="now")
    ]
```

Import `Action` and `parse_fallback` at the top, and delete the tests that assert the removed `Intent`/`parse_intent` API. Keep `normalize_playlist_name`'s tests unchanged.

Also in this task, remove the now-dead `Intent` path: in
`services/bot/tests/test_voice_control.py` delete the tests that call
`dispatcher.dispatch(Intent(...))` (their behavior is covered by the
`dispatch_all` tests added in Task 3), and in
`services/bot/src/jacky/voice_control.py` delete `dispatch()`, `_search()` and
`_playlist()` along with the `Intent` import. Grep for `parse_intent` and
`Intent(` across `services/bot/` afterwards and confirm the only remaining hit
is `control.py`'s import, which Task 5 replaces.

- [ ] **Step 2: Run to verify failure**

Run: `py -m pytest tests/test_voice_intent.py -q`
Expected: FAIL — `ImportError: cannot import name 'parse_fallback'`.

- [ ] **Step 3: Implement.** In `services/bot/src/jacky/api/voice_intent.py`, replace `parse_intent` with `parse_fallback` returning `list[Action]`, keeping the existing normalization helpers and `normalize_playlist_name`. Delete the `Intent` dataclass — nothing uses it after this task.

```python
from jacky.api.voice_actions import Action

_EXACT = {
    "skip": Action("skip"),
    "next": Action("skip"),
    "skip track": Action("skip"),
    "pause": Action("pause"),
    # Stop-like speech pauses: ending a session is not a voice capability.
    "stop": Action("pause"),
    "resume": Action("resume"),
    "unpause": Action("resume"),
    "continue": Action("resume"),
    "louder": Action("volume", delta=10),
    "volume up": Action("volume", delta=10),
    "turn it up": Action("volume", delta=10),
    "quieter": Action("volume", delta=-10),
    "volume down": Action("volume", delta=-10),
    "turn it down": Action("volume", delta=-10),
    "shuffle": Action("shuffle"),
    "shuffle the queue": Action("shuffle"),
    "clear the queue": Action("clear_queue"),
    "clear queue": Action("clear_queue"),
}

_PLAYLIST_PREFIXES = (
    ("play playlist ", "now"),
    ("add playlist ", "end"),
    ("queue playlist ", "end"),
)
_PLAY_PREFIXES = (("play ", "now"), ("add ", "end"), ("queue ", "end"))


def parse_fallback(transcript: str) -> list[Action]:
    """Offline path used when the LLM is unreachable. Same semantics as the
    LLM vocabulary, so both paths agree on what 'play' means."""
    text = transcript.strip().strip(".!?,").strip()
    if not text:
        return []
    lowered = text.lower()
    norm = _WS.sub(" ", _PUNCT.sub(" ", lowered)).strip()
    if norm in _EXACT:
        return [_EXACT[norm]]

    for prefix, placement in _PLAYLIST_PREFIXES:
        if lowered.startswith(prefix):
            name = text[len(prefix):].strip()
            if name:
                return [Action("playlist", name=name, placement=placement)]

    for prefix, placement in _PLAY_PREFIXES:
        if lowered.startswith(prefix):
            query = text[len(prefix):].strip()
            if query and query.lower() != "playlist":
                # A trailing "next" overrides the verb's placement.
                if query.lower().endswith(" next"):
                    return [Action("play", query=query[:-5].strip(), placement="next")]
                return [Action("play", query=query, placement=placement)]

    return [Action("play", query=text, placement="now")]
```

- [ ] **Step 4: Verify.** `py -m pytest -q` and ruff. Note `control.py` still imports `parse_intent` and will fail — that is expected and fixed in Task 5; if the suite cannot even import, proceed to Task 5 and run the gates there.

- [ ] **Step 5: Commit**

```bash
git add services/bot/src/jacky/api/voice_intent.py services/bot/tests/test_voice_intent.py
git commit -m "feat(voice): fallback parser emits actions with the new placements"
```

## Task 5: Route — LLM, fallback, per-action logging

**Files:** `services/bot/src/jacky/api/control.py`, `services/bot/tests/test_control_api.py`

- [ ] **Step 1: Write the failing tests.** In `services/bot/tests/test_control_api.py`, replace the voice route tests' expectations and add:

```python
class FakeInterpreter:
    def __init__(self):
        self.actions, self.error, self.calls = None, None, []

    async def interpret(self, transcript):
        self.calls.append(transcript)
        if self.error:
            raise self.error
        from jacky.api.voice_actions import Action
        return self.actions or [Action("skip")]


@pytest.fixture
def interpreter():
    return FakeInterpreter()
```

Wire `interpreter` into `build_client`/`client` the same way `transcriber` is, passing `interpreter=interpreter` to `register_control_routes`. Then:

```python
async def test_voice_runs_every_action_in_order(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    from jacky.api.voice_actions import Action

    put_user_in_voice(service, guild_id)
    transcriber.text = "skip then pause"
    interpreter.actions = [Action("skip"), Action("pause")]
    body = await (await client.post("/control/voice", data=WAV, headers=auth)).json()
    assert body["ok"] is True
    assert [a["action"] for a in body["actions"]] == ["skip", "pause"]
    assert (await service.repo.get_state(sid))["isPaused"] is True


async def test_voice_logs_one_history_row_per_action_sharing_the_transcript(
    client, service, guild_id, auth, transcriber, interpreter
):
    from jacky.api.voice_actions import Action

    put_user_in_voice(service, guild_id)
    transcriber.text = "skip then pause"
    interpreter.actions = [Action("skip"), Action("pause")]
    await client.post("/control/voice", data=WAV, headers=auth)
    rows = service.repo.command_log[-2:]
    assert [r[1] for r in rows] == ["skip", "pause"]
    assert all(r[4] == "voice" and r[5] == "skip then pause" for r in rows)


async def test_voice_falls_back_to_the_parser_when_the_llm_fails(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    """An OpenAI outage must degrade to basic commands, not break the key."""
    from jacky.api.voice_llm import InterpretError

    put_user_in_voice(service, guild_id)
    interpreter.error = InterpretError("down")
    transcriber.text = "skip"
    resp = await client.post("/control/voice", data=WAV, headers=auth)
    assert resp.status == 200
    assert (await resp.json())["actions"][0]["action"] == "skip"
    assert service.node.updates[-1] == (guild_id, {"track": {"encoded": None}})


async def test_voice_reports_a_partial_summary(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    from jacky.api.voice_actions import Action
    from jacky.audio.models import LoadResult

    put_user_in_voice(service, guild_id)
    service.node.load_results["ytsearch:nothing"] = LoadResult(kind="empty", tracks=[])
    await service.repo.update_state(sid, {"currentTrack": {"title": "Now"}})
    interpreter.actions = [
        Action("play", query="nothing", placement="end"),
        Action("pause"),
    ]
    body = await (await client.post("/control/voice", data=WAV, headers=auth)).json()
    assert body["ok"] is False
    assert "1 of 2" in body["detail"]


async def test_voice_422_when_nothing_survives_validation(
    client, service, guild_id, auth, transcriber, interpreter
):
    from jacky.api.voice_llm import InterpretError

    put_user_in_voice(service, guild_id)
    interpreter.error = InterpretError("down")
    transcriber.text = "..."      # fallback parser yields nothing
    resp = await client.post("/control/voice", data=WAV, headers=auth)
    assert resp.status == 422
```

- [ ] **Step 2: Run to verify failure**

Run: `py -m pytest tests/test_control_api.py -q -k voice`
Expected: FAIL — `register_control_routes() got an unexpected keyword argument 'interpreter'`.

- [ ] **Step 3: Implement.** In `control.py`: add `interpreter: Any = None` to the signature, change the import to `from jacky.api.voice_intent import parse_fallback`, and replace the body of `voice` from the `intent = parse_intent(...)` line to the response with:

```python
        try:
            actions = await interpreter.interpret(transcript) if interpreter else []
        except Exception:  # noqa: BLE001 — degrade, don't fail
            log.warning("voice interpretation failed; using the fallback parser")
            actions = []
        if not actions:
            actions = parse_fallback(transcript)
        if not actions:
            return web.json_response({"error": "no-speech"}, status=422)

        results = await voice_dispatcher.dispatch_all(guild.id, actions)
        for action, result in zip(actions, results, strict=False):
            # Logged as the EXECUTED action so the dashboard's retrigger
            # works, with the transcript alongside. One row per action, all
            # sharing the utterance that produced them.
            log_args = result.log_arg if result.log_arg is not None else (
                action.query or action.name
            )
            await service.repo.log_command(
                str(guild.id), _LOG_COMMAND_FOR.get(action.action, action.action),
                log_args, "Voice", user_id,
                source="voice", transcript=transcript,
            )
        done = sum(1 for r in results if r.ok)
        return web.json_response({
            "transcript": transcript,
            "actions": [
                {"action": a.action, "ok": r.ok, "detail": r.detail}
                for a, r in zip(actions, results, strict=False)
            ],
            "ok": done == len(results),
            "detail": results[0].detail if len(results) == 1
                      else f"{done} of {len(results)} done",
        })
```

Update `_LOG_COMMAND_FOR` to the new verbs:

```python
_LOG_COMMAND_FOR = {
    "play": "play",
    "playlist": "playlist",
    "volume": "volume",
    "clear_queue": "clear",
}
```

- [ ] **Step 4: Verify.** `py -m pytest -q` and ruff. The auth sweep count is unchanged (no new route).

- [ ] **Step 5: Commit**

```bash
git add services/bot/src/jacky/api/control.py services/bot/tests/test_control_api.py
git commit -m "feat(voice): run interpreted action lists with a parser fallback"
```

## Task 6: Config, wiring, deploy, plugin type

**Files:** `services/bot/src/jacky/config.py`, `core/bot.py`, `tests/test_config.py`, `deploy/docker-compose.yml`, `deploy/.env.example`, `streamdeck-plugin/src/api-client.ts`

- [ ] **Step 1: Config.** Add `openai_intent_model: str` to `Settings`, and in `from_env()`:

```python
            openai_intent_model=(
                os.environ.get("OPENAI_INTENT_MODEL") or "gpt-4o-mini"
            ),
```

Extend `test_openai_settings_default_and_override` to assert the empty-string case falls back to `gpt-4o-mini` (the same compose `${VAR:-}` trap the other settings guard against).

- [ ] **Step 2: Wiring.** In `core/bot.py`, inside the block that builds the transcriber, also build the interpreter and pass it:

```python
                from jacky.api.voice_llm import LlmIntentInterpreter

                interpreter = LlmIntentInterpreter(
                    self.http_session,
                    self.settings.openai_api_key,
                    self.settings.openai_intent_model,
                )
```

and add `interpreter=interpreter` to `register_control_routes(...)` (initialise `interpreter = None` alongside `transcriber = None`).

- [ ] **Step 3: Deploy contract.** In `deploy/docker-compose.yml` bot env: `OPENAI_INTENT_MODEL: ${OPENAI_INTENT_MODEL:-}`. In `deploy/.env.example`, under the existing voice section:

```
# Override the intent-interpretation model. Default: gpt-4o-mini
#OPENAI_INTENT_MODEL=gpt-4o
```

- [ ] **Step 4: Plugin type.** In `streamdeck-plugin/src/api-client.ts`, update `VoiceResult`:

```ts
export type VoiceResult = {
  transcript: string;
  actions: { action: string; ok: boolean; detail: string }[];
  ok: boolean;
  detail: string | null;
};
```

The key already renders `detail`, so `src/actions/voice.ts` needs no change — verify that by reading it rather than assuming.

- [ ] **Step 5: Verify.** `cd services/bot && py -m pytest -q` + ruff; from `deploy/`, `docker compose --env-file .env.ci-test config --quiet` and `--env-file .env.example config --quiet` both exit 0; `cd streamdeck-plugin && npm test && npm run build && npx tsc --noEmit`.

- [ ] **Step 6: Commit**

```bash
git add services/bot deploy streamdeck-plugin/src/api-client.ts
git commit -m "feat(voice): wire the interpreter, env contract, and result type"
```

## Task 7: Docs, deploy, verify, pack

- [ ] **Step 1: Runbook.** In `docs/streamdeck-control.md`, replace the Voice Command grammar table with the new semantics:

```markdown
  | Say | Result |
  |---|---|
  | "play X" | Interrupts and plays X now |
  | "play X next" | Puts X at the front of the queue |
  | "add X" / "queue X" | Appends X to the end |
  | "play/add playlist NAME" | Same three placements for a saved playlist |
  | "skip", "skip two" | Skip one or several |
  | "pause", "resume", "stop the music" | Stop-like speech pauses |
  | "louder", "volume 40" | Relative or absolute |
  | "shuffle", "clear the queue" | Queue controls |
  | "repeat this song" | Loop mode |

  Speech is interpreted by an LLM, so phrasing is flexible and several
  instructions in one breath work ("skip this and add two songs by X").
  Search terms are taken literally — it will not invent music you didn't name.
  Nothing can be deleted by voice except clearing the current queue.
```

- [ ] **Step 2: Merge and deploy.**

```bash
git checkout master && git merge --no-ff feat/voice-llm-intent -m "Merge feat/voice-llm-intent: LLM voice interpretation" && git push origin master
```

Then on the VM (no new secret — `OPENAI_API_KEY` already present):

```bash
gcloud compute ssh personal-project-machine --project=personal-server-492701 --zone=us-east1-b --command="cd ~/discord-music-bot && sudo git -c safe.directory=\$PWD pull origin master && sudo docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build bot"
```

- [ ] **Step 3: Verify live.** `curl -s -o /dev/null -w "%{http_code}\n" -X POST "https://control.jacky-music-bot.com/control/voice"` → `401`.

- [ ] **Step 4: Pack.**

```bash
cd streamdeck-plugin && npm run fetch-ffmpeg && npm run build \
  && rm -f com.jacobchoi.jacky-control.streamDeckPlugin \
  && npx @elgato/cli pack com.jacobchoi.jacky-control.sdPlugin --force
```

Then `git checkout -- streamdeck-plugin/com.jacobchoi.jacky-control.sdPlugin/manifest.json` (pack reformats it) and deliver the file.

- [ ] **Step 5: User walkthrough.** "play X" interrupts; "play X next" doesn't; "add X" appends; "shuffle"; "clear the queue"; "stop the music" pauses rather than ending the session; a two-command utterance; an utterance naming a nonexistent song alongside a valid one (the valid one still runs); and confirm the dashboard shows one history row per action, all carrying the same transcript.
