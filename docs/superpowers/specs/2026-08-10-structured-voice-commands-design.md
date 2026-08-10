# Structured Voice Commands with a Reasoning Layer — Design

**Date:** 2026-08-10
**Status:** Approved
**Scope:** Replaces free-form LLM interpretation of voice commands with a structure-first hybrid: a deterministic grammar decides whenever it can, and the LLM only reasons about what the grammar could not resolve. Adds a fixed transcription language. Touches `services/bot/` and `streamdeck-plugin/`.

## Problem

Voice commands frequently search YouTube for the words that were spoken, even when searching was never the intent. Two mechanisms cause it, and both are by design in the current code:

1. **`parse_fallback` ends in a catch-all**: *"anything matching no command becomes a search query."*
2. **The LLM prompt says the same thing**: *"If the user simply names music with no verb, treat it as play 'now'."*

So "next song" becomes a search for **song**, and any misheard phrase becomes a search that also **interrupts the current track**. The failure is not that the model reasons badly — it is that both paths are instructed to treat "I don't know" as "search". A command surface where the default action is destructive to what is currently playing will feel wrong however good the model is.

The fix is not a better prompt. It is: **the grammar decides when it can, reasoning fills the gap, and "I don't know" does nothing.**

## Decisions

| Question | Decision |
|---|---|
| Order of resolution | **Structure first.** A deterministic parser recognises the closed vocabulary and returns actions with no LLM call — free, instant, and not subject to a model changing its mind. |
| Role of the LLM | Only for what the grammar could not resolve, and only as a **classifier onto the same closed grammar** — never as a free generator. It is told which vocabulary words appeared and where. |
| The unknown case | **Nothing runs.** The key reports "Didn't catch that". There is no longer any path from an unrecognised utterance to a search. |
| Interrupting | The current track may be replaced **only** when the transcript actually contains "play", or the resolved action is a skip. Enforced in validation, not in the prompt. |
| `playlist` | If the utterance contains "playlist", the action is a playlist action against the session's saved playlists — never a YouTube search. Enforced structurally. |
| Placement | `play X` → replaces now · `play X next` → front of queue · `add X` / `queue X` → end. Identical for playlists. |
| Short forms | Bare "session code", "volume 50", "clear", "shuffle" resolve on their own. |
| "next song" | Skip. The grammar knows `next` + a filler noun is media control, not a search for "song". |
| Transcription language | Fixed, defaulting to **English**, sent per request. The Voice key gets a language setting so Korean (or any supported language) is a per-key choice. |

## The grammar

Recognised deterministically, in this order — the first match wins:

| Spoken | Action |
|---|---|
| `skip`, `next`, `next song`, `next track`, `skip 2` | skip (n) |
| `pause`, `stop`, `resume`, `continue` | pause / resume |
| `shuffle`, `shuffle the queue` | shuffle |
| `clear`, `clear the queue` | clear queue |
| `volume 50`, `volume up`, `louder`, `quieter` | absolute / relative volume |
| `session code`, `post the session code` | post session info to Discord |
| `what's playing`, `now playing` | post current track to Discord |
| `open the dashboard` | open dashboard (client directive) |
| `repeat`, `loop track`, `loop queue`, `loop off` | loop mode |
| `play playlist X` / `X next` / `add playlist X` | playlist, placement now / next / end |
| `play X` / `play X next` / `add X` / `queue X` | search, placement now / next / end |

Anything else is **unresolved** and goes to the reasoning layer.

Note "next" is overloaded on purpose, and the grammar resolves it by position: leading `next` is media control ("next", "next song"); trailing `next` is placement ("play X next"). That single rule is what stops "next song" becoming a search.

## Components

### 1. `jacky/api/voice_grammar.py` (new, pure — tested)

`parse_structured(transcript) -> StructuredParse` where

```python
@dataclass(frozen=True)
class StructuredParse:
    actions: list[Action]      # empty when unresolved
    keywords: list[str]        # vocabulary words found, for the LLM's hints
    resolved: bool
```

Pure, no network. This is where the table above lives, and it replaces `voice_intent.parse_fallback`'s catch-all — the free-form branch is deleted rather than reordered, because a catch-all that only runs "sometimes" is the same bug with a smaller blast radius.

### 2. `jacky/api/voice_llm.py` (reworked)

The prompt becomes a **classification** brief, not a generation brief:

- it is given the transcript **plus the vocabulary words the grammar found**, so structure informs reasoning rather than competing with it;
- it is told the disambiguation rules explicitly — leading "next" is skip, "playlist" always means the saved-playlist list, a bare noun phrase with no verb is **not** a search;
- it may return an empty action list, and doing so is a correct answer. The "if in doubt, search" instruction is removed.

The structured-output schema is unchanged, so the security boundary (`validate_actions`, closed vocabulary, no deletion verb) is untouched.

### 3. `jacky/api/voice_actions.py` (one new rule)

`enforce_intent(actions, transcript)` — applied after `validate_actions`, as a second, purely structural pass:

- a `play` action with placement `now` is downgraded to `end` unless the transcript contains "play";
- a `play` action is converted to a `playlist` action when the transcript contains "playlist";
- everything else passes through.

This is the guarantee the user actually asked for — *"the currently playing track should only be overridden if my command includes play"* — expressed as code rather than as a request to the model.

### 4. Transcription language

`OpenAITranscriber.transcribe(wav, language)` sends OpenAI's `language` field. Fixing the language materially improves accuracy versus autodetect on short clips, which is the whole reason for the setting.

`POST /control/voice` accepts `?language=xx`, validated against a small allowlist (`en`, `ko`, `ja`, `es`, `fr`, `de`, `zh`), defaulting to `en`. An unknown value falls back to `en` rather than erroring — a bad setting should degrade, not break the key.

### 5. Plugin

The Voice key's Property Inspector gains a **Language** dropdown, English by default. `voiceCommand(wav, language)` passes it as a query parameter.

## Error handling

| Condition | Result |
|---|---|
| Grammar resolves it | Runs; **no LLM call, no cost, no latency** |
| Grammar unresolved, LLM resolves it | Runs |
| Grammar unresolved, LLM returns nothing | **"Didn't catch that"** — nothing runs, nothing is searched |
| LLM unreachable and grammar unresolved | Same as above. The grammar *is* the fallback now, so an outage costs reasoning, not the key |
| "play" absent but the model asked to interrupt | Downgraded to end-of-queue |
| "playlist" present but the model chose a search | Converted to a playlist action |
| Unknown language setting | Falls back to English |

## Testing

- **Grammar:** every row of the table; leading vs trailing "next"; `volume 50` and `volume up`; bare "session code" and "clear"; playlist placements; and — the regression that matters — **an unrecognised phrase resolves to nothing, never a search**.
- **`enforce_intent`:** "play" absent ⇒ never placement `now`; "playlist" present ⇒ never a search; a legitimate `play X` keeps `now`.
- **Interpreter:** the prompt carries the keyword hints; an empty action list is accepted as a valid answer rather than raising.
- **Route:** grammar-resolved input never calls the interpreter (assert the fake is untouched — this is both the cost and the accuracy win); unresolved input does; nothing resolvable ⇒ 422 and no dispatch.
- **Transcriber:** the language field is sent; an unknown language becomes `en`.
- **Manual:** "next song" skips; "session code" posts; "volume 50" sets 50; "play playlist chill" loads the saved playlist; "add X" appends without interrupting; a mumble does nothing.

## Known limitation: the grammar is English-only

The deterministic grammar's tables — media control, volume, session code, the
`play`/`add`/`queue` prefixes — are English strings. So the "resolved instantly,
no model, no cost" path exists **only in English**. Every utterance in the other
six offered languages falls through to the reasoning layer, which means latency
and an API call on every press, plus full exposure to the interpreter's failure
modes.

`enforce_intent` understands play verbs in all seven languages, so a
non-English speaker is not silently prevented from replacing the current track
— that bug existed and is fixed. But they do not get the deterministic path,
and pretending otherwise would misrepresent what the language setting buys:
today it buys **transcription accuracy**, not grammar coverage.

Translating the grammar tables is the obvious next step if non-English use
turns out to be common.

## Out of scope

Wake words, multi-language autodetect, per-user language, and any change to the action vocabulary itself.
