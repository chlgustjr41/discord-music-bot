# Voice Command — LLM Intent Interpretation — Design

**Date:** 2026-08-08
**Status:** Approved
**Scope:** Replaces the deterministic grammar in the voice command key with LLM interpretation, changes `play` to interrupt-and-play, and widens the action vocabulary to cover the session dashboard's non-destructive controls. Touches `services/bot/` and a small part of `streamdeck-plugin/` (result rendering). No changes to audio capture, auth, the tunnel, or the deploy contract beyond one optional env var.

## Problem

The shipped voice key works, but two things are wrong for real use:

1. **`play` queues instead of playing.** Saying "play X" should interrupt and play X immediately; "play X next" should jump the queue without interrupting; "add X" should append. Today all three queue.
2. **The grammar is too rigid.** Structured phrases were the right call for v1 (they are predictable and free), but in use the constraint bites: natural phrasings miss, several instructions in one breath are impossible, and dashboard capabilities like shuffling have no voice path at all.

The fix is to interpret intent with an LLM while keeping every other layer — capture, transcription, dispatch, logging — exactly as it is. **The current setup works; this enhances only the intent stage.**

## Decisions

| Question | Decision |
|---|---|
| Interpretation | `gpt-4o-mini` with **structured outputs** (a strict JSON schema, enforced at decode time), not free-text parsing. Adds ~$0.0001 and ~0.5–1 s per command. |
| Action vocabulary | A **closed set** (below). The schema has no verb for deleting anything, so no phrasing — accidental or adversarial — can produce a destructive action. |
| Trust model | The model's output is **untrusted input**: every action is re-validated server-side against the same vocabulary before dispatch. A schema the model *should* obey is not the same as one it *did*. |
| `play` semantics | `play X` → **interrupt and play now**; `play X next` → front of queue, no interruption; `add X` → end of queue. Same three placements for playlists. |
| Destructive actions | None reachable except `clear_queue`, which empties **only the current queue**. Stop-like speech ("stop the music") maps to **pause**. Ending a session stays on the dedicated Stop key. Playlists, history, and sessions can never be deleted by voice. |
| Multi-command | Up to **5** actions per utterance, executed in order. A failure does not block the rest; the key reports a summary ("2 of 3 done"). |
| Latitude | **Literal.** Search terms are extracted verbatim; the model never invents tracks. "Play something chill" searches for "something chill". |
| Fallback | The existing deterministic parser is **kept**, updated to the new placement semantics, and used when the LLM call fails or times out — so an OpenAI outage degrades to basic commands instead of breaking the key. |
| Interrupted track | Dropped, not requeued — "play X" is explicitly "skip the current track and play X". |

## Action vocabulary

The complete set the model may emit. `placement` defaults to `now` for `play`/`playlist`.

| Action | Fields | Effect |
|---|---|---|
| `play` | `query`, `placement: now\|next\|end` | Resolve and play now / insert at front / append |
| `playlist` | `name`, `placement: now\|next\|end` | Same three placements for a saved playlist |
| `skip` | `count` (default 1, max 10) | Skip N tracks |
| `pause` / `resume` | — | Pause / resume |
| `volume` | `level` (0–100) **or** `delta` | Absolute or relative |
| `shuffle` | — | Shuffle the queue |
| `clear_queue` | — | Empty the queue (current track keeps playing) |
| `loop` | `mode: off\|track\|queue` | Set loop mode |

Anything else — including any deletion — is not expressible.

## Components

### 1. Bot — `jacky/api/voice_llm.py` (new)

`LlmIntentInterpreter.interpret(transcript) -> list[Action]`, injectable exactly like the transcriber so tests never reach the network.

- Posts to OpenAI chat completions with `response_format: {"type": "json_schema", "json_schema": {..., "strict": true}}`.
- System prompt states the rules the schema cannot express: literal extraction only, stop→pause, default placement `now`, at most 5 actions, and that ordering is significant.
- Raises `InterpretError` on transport failure, non-200, malformed JSON, or an empty action list.

### 2. Bot — `jacky/api/voice_actions.py` (new, pure)

The vocabulary in one place: an `Action` dataclass, the JSON schema literal, and `validate_actions(raw) -> list[Action]` which drops anything unknown or out of range and truncates to 5. Pure and heavily tested — this is the security boundary, so it is deliberately separate from the module that talks to the network.

### 3. Bot — `jacky/voice_control.py` (extended)

`VoiceIntentDispatcher.dispatch_all(guild_id, actions) -> list[DispatchResult]` runs actions in order, catching per-action failures so one bad track cannot cancel the rest.

New/changed handlers:
- `play` with `placement="now"` → resolve, then `start_current_track` (replaces playback; Lavalink reports `replaced`, which `on_track_end` already ignores, so no advance race).
- `placement="next"` → queue write with the track at the front; `placement="end"` → append.
- `playlist` reuses the existing front/append logic, plus `now` (insert at front then jump).
- `shuffle`, `clear_queue`, `loop`, absolute `volume`.
- `skip` with `count > 1` **pops `count - 1` entries from the queue and then
  skips once** — it does not call `skip()` repeatedly. Repeated calls would
  race the `TrackEnd`-driven auto-advance (each skip triggers `play_next`
  asynchronously), so N rapid skips would drop an unpredictable number of
  tracks. Popping first makes the count exact.

All queue writes keep the established ordering rule: read state **before** the write, no await between the write and any start call, so the Firestore listener cannot pop a track that was just inserted.

### 4. Bot — `POST /control/voice` (extended)

Transcript → `LlmIntentInterpreter` → on `InterpretError`, fall back to `parse_intent` (adapted to one `Action`) → `validate_actions` → `dispatch_all` → one command-history row per executed action, each carrying the same transcript → response:

```json
{"transcript": "...", "actions": [{"action": "...", "ok": true, "detail": "..."}], "ok": true, "detail": "2 of 3 done"}
```

`detail` is a single summary line for the key. The existing `intent` field is dropped; the plugin is updated in step with it.

### 5. Bot — `jacky/api/voice_intent.py` (fallback, updated)

Kept as the offline path. `play X` now yields placement `now`, `play X next` → `next`, `add`/`queue X` → `end`, so both paths agree on semantics.

### 6. Plugin

`VoiceResult` gains `actions` and keeps `transcript`/`ok`/`detail`; the key renders `detail`. No changes to capture, the key lifecycle, or the microphone handling — that code is settled and out of scope.

### 7. Config

`OPENAI_INTENT_MODEL` (default `gpt-4o-mini`), passed through compose alongside the existing OpenAI vars. Interpretation reuses `OPENAI_API_KEY`; no new credential.

## Error handling

| Condition | Result |
|---|---|
| LLM unreachable / non-200 / malformed | Falls back to the deterministic parser; key behaves as today |
| Model emits an unknown or out-of-range action | Dropped by `validate_actions`, remaining actions still run |
| More than 5 actions | Truncated to 5; the key reports how many ran |
| One action fails mid-sequence | Remaining actions still run; summary reports "N of M" |
| No actions at all after validation | 422 `no-speech` (same as an empty transcript — nothing actionable was said) |
| Everything else (auth, 409, 413, 502, 503) | Unchanged |

## Security

- **Deletion is unreachable by construction.** No verb exists for removing playlists, history, or sessions. `clear_queue` is the only destructive action and is bounded to the current queue.
- **Model output is untrusted.** `validate_actions` re-checks every action against the vocabulary and clamps every numeric field, independently of what the schema was supposed to guarantee.
- Blast radius is bounded three ways: the closed vocabulary, the 5-action cap, and per-action clamps (`skip` ≤ 10, `volume` 0–100).
- The transcript is sent to OpenAI for interpretation as well as transcription — the same data to the same vendor, no new exposure. Audio still never touches disk; transcripts still go only to the session's command history and never to container stdout (the existing dispatch `INVARIANT` comment continues to apply).

## Testing

- **`validate_actions` (pure, the security boundary):** unknown verb dropped; deletion-shaped input (`{"action": "delete_playlist"}`) rejected; >5 truncated; `skip` count and `volume` level clamped; malformed entries skipped without killing the batch; empty result handled.
- **Interpreter:** faked HTTP; asserts the request carries `strict: true` and the vocabulary schema; parses a well-formed response; `InterpretError` on non-200, bad JSON, and empty actions.
- **Route:** LLM path runs actions in order; LLM failure falls back to the deterministic parser (asserted by a faked interpreter that raises); one history row per action, all sharing the transcript; multi-action summary; 422 when nothing survives validation.
- **Dispatcher:** each new action and every placement, including that `play now` replaces the current track and that `placement=end` never interrupts; queue-write ordering preserved.
- **Fallback parser:** updated placement expectations.
- **Manual:** "play X" interrupts; "play X next" doesn't; "add X" appends; "shuffle"; "clear the queue"; "stop the music" pauses rather than ending the session; a two-command utterance; an utterance naming a nonexistent song alongside a valid one (the valid one still runs).

## Out of scope

Removing or reordering individual queue entries by voice (awkward and error-prone without indices), the LLM choosing music itself, wake-word/always-listening, multi-language, and any change to audio capture or the key lifecycle.
