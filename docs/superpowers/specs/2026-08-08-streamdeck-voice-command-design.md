# Stream Deck Voice Command Key — Design

**Date:** 2026-08-08
**Status:** Approved
**Scope:** A `.voice` push-to-talk key in `streamdeck-plugin/`, one new guarded route on the bot's control API, a deterministic intent parser, and the `VoiceIntentDispatcher` ported from the shelved `feat/voice-control` branch. No changes to auth, the tunnel, the Firestore contract, or the web app.

## Problem

Queueing a track from the deck means configuring a key per playlist or reaching for Discord. Voice is the natural input for "add this song", but the 2026-07 attempt at voice control was shelved.

**Why that attempt failed, and why this one is different.** The blocker was never speech recognition: `discord-ext-voice-recv` 0.5.2a179 produced corrupt audio — with zero packet loss and contiguous RTP sequence numbers, ~49% of decrypted Opus frames failed to decode and the remainder decoded to garbage, so even a full-vocabulary Vosk recognizer transcribed nothing from 23 s of clear speech. The failure lived in the **audio acquisition** layer (an alpha library chasing Discord's changing voice encryption); the intent and playback layers above it were correct and tested.

This design **removes that layer from the architecture** rather than working around it. Audio comes from the user's own microphone via the Stream Deck host. Discord voice receive, the second bot user, the wake phrase, the VAD gate, and the passive/active grammar state machine all disappear — push-to-talk delimits the utterance. A direct mic feed is also cleaner than audio round-tripped through Discord's Opus encoding, so accuracy should exceed what the original design could have reached.

**Verified before committing to this design** (the explicit lesson from the failure — instrument the real acquisition path early, never validate with clean sample files): ffmpeg is installed on the target machine, enumerates the mics, and Node spawns it and receives a valid 16 kHz mono WAV on stdout with a clean exit. Measured: **~1–1.8 s of DirectShow device warmup** before audio flows, which the key's feedback design accounts for.

## Decisions

| Question | Decision |
|---|---|
| Audio capture | ffmpeg (`-f dshow`) spawned from the plugin's Node runtime; WAV on stdout, 16 kHz mono. ffmpeg must be on PATH — a hard prerequisite, surfaced as a clear key error when missing. |
| Push-to-talk | `onKeyDown` starts capture, `onKeyUp` stops it (`q` on stdin, SIGKILL fallback). Hard cap 15 s. |
| Warmup handling | The key shows "Listening…" only once the first PCM bytes arrive, so the user knows when to speak. The mic is **not** pre-warmed: keeping it open continuously would defeat the privacy property that makes push-to-talk acceptable. |
| Where STT runs | On the bot, via OpenAI `POST /v1/audio/transcriptions`, model `gpt-4o-mini-transcribe` (~$0.003/min). Keeps the API key server-side, reuses existing auth, and costs the 2-vCPU VM nothing. |
| Intent parsing | **Deterministic ordered matcher**, not an LLM. The requirement is "structured phrases for consistent behavior"; a rules table is predictable, free, instant, and unit-testable. |
| Free-form case | Only song search. Any transcript that matches no command is treated as a search query. |
| Destructive commands | `stop` is **excluded** from voice. One misrecognition would end the session and clear the queue with no undo, and a dedicated Stop key already exists. Summon and dashboard are also excluded (not playback). |
| Playlist placement | The verb decides: `play playlist X` inserts at the front and jumps (matching the Playlist key); `add`/`queue playlist X` appends. |
| Confirmation | None — auto-run, with the recognized text shown on the key. A misheard song queues a wrong track, which `skip` fixes. |

## Grammar

Matched case-insensitively against the transcript after lowercasing, stripping punctuation, and collapsing whitespace. **Order matters** — the first match wins, and the bare-text fallback is last.

| Pattern | Intent | Argument |
|---|---|---|
| `skip` \| `next` \| `skip track` | `skip` | — |
| `pause` | `pause` | — |
| `resume` \| `unpause` \| `continue` | `resume` | — |
| `volume up` \| `louder` \| `turn it up` | `volume_up` | — |
| `volume down` \| `quieter` \| `turn it down` | `volume_down` | — |
| `(play\|add\|queue) playlist <name>` | `playlist_play` (verb `play`) / `playlist_add` (`add`/`queue`) | `<name>` |
| `(play\|add\|queue) <query>` | `search` | `<query>` |
| *anything else, non-empty* | `search` | the whole transcript |

`playlist` is a keyword: "play playlist chill" loads the saved playlist; "play chill" searches. Playlist names are matched against saved names by a normalized comparison (lowercase, punctuation and spaces removed), so "chill vibes" finds "Chill Vibes". A playlist command that matches nothing returns `no-such-playlist` — it does **not** fall back to a song search, because the user explicitly said "playlist".

Accepted trade-off of the bare-text fallback: a badly misheard command ("pause" → "paws") becomes a search rather than an error. Recoverable with `skip`.

## Components

### 1. Bot — `POST /control/voice`

Guarded like every control route (bearer → TokenStore → rate limit), handler `(request, user_id)`.

- **Request:** raw WAV body, `Content-Type: audio/wav`. Rejected with 413 above `VOICE_MAX_BYTES` (600 KB ≈ 18 s at 16 kHz mono — comfortably above the client's 15 s cap).
- **Session:** `resolve_guild(user_id)` — the caller's live session, identical to the other action keys. No session → 409 `no-active-session`, before any transcription (never pay for a request that cannot succeed).
- **Transcription:** `jacky/api/transcribe.py` — a thin injectable client posting multipart to OpenAI. Failure → 502 `stt-failed`. Empty/whitespace transcript → 422 `no-speech`.
- **Dispatch:** parse → `VoiceIntentDispatcher`.
- **Response 200:** `{"transcript": str, "intent": str, "ok": bool, "detail": str | null}` — `detail` carries what to show on the key (e.g. the queued track title, or "No playlist called X").

### 2. Bot — `jacky/api/voice_intent.py` (new, pure)

`parse_intent(transcript) -> Intent(kind, arg)`. No I/O, no dependencies; the grammar table above lives here and is the primary test target.

### 3. Bot — `jacky/voice_control.py` (ported)

`VoiceIntentDispatcher` from `feat/voice-control`, with `stop` removed and the two playlist intents added.

Both playlist intents follow the ordering already proven in `play_playlist`:
decide from state read **before** the queue write, with no await between the
write and the start call, so the Firestore listener cannot pop the track just
inserted. They differ only in placement and what follows:

- `playlist_play` — tracks go to the **front**; then `skip()` if something is
  playing (jump to it), else `play_next()`.
- `playlist_add` — tracks go to the **end**; playback starts only if nothing
  is currently playing, otherwise the queue simply grows. Appending must never
  interrupt the current track.

### 4. Plugin

- **`src/audio-capture.ts` (new):** `buildFfmpegArgs(device)` (pure, testable) and a `MicRecorder` class wrapping spawn/collect/stop with the 15 s cap and a first-bytes callback that drives the "Listening…" transition.
- **`src/actions/voice.ts` (new):** UUID `.voice`. `onKeyDown` → record; `onKeyUp` → stop, POST, render result. Hold shorter than the warmup, or zero bytes captured → ⚠ + "hold longer".
- **`api-client.ts`:** `voiceCommand(wav: Uint8Array)`.
- **`pi-bridge.ts` / PI:** `get-audio-devices` → the plugin runs `ffmpeg -list_devices` and returns the audio device names for a dropdown (`inputDevice`, per-action setting). Purely plugin-side; the bot is not involved.
- **Manifest:** `.voice` action, single state, Version → `0.4.0.0`, new `imgs/voice.svg` (mic glyph in the house style).

### 5. Config & deploy

`OPENAI_API_KEY` (already in `deploy/.env`) and optional `OPENAI_STT_MODEL` (default `gpt-4o-mini-transcribe`); both passed through in `docker-compose.yml`. The route registers only when the key is present — same graceful-disable pattern as the OAuth gate — so a deployment without it simply lacks the voice route.

## Error handling

| Condition | Result |
|---|---|
| ffmpeg missing / device invalid | Key shows "No mic" + ⚠; nothing sent |
| Hold too short / no audio | "Hold longer" + ⚠; nothing sent |
| No live session | 409 → ⚠ |
| Body too large | 413 → ⚠ |
| Transcription API failure | 502 → "STT failed" |
| Empty transcript | 422 → "Didn't catch that" |
| Unknown playlist | 200 `ok:false` → "No playlist called X" |
| Search found nothing | 200 `ok:false` → "No results" |

Every failure is visible on the key and leaves playback untouched.

## Security & privacy

- No new exposure: one more route behind the same bearer token, rate limiter, and tunnel.
- The microphone is opened only while the key is held, and released on `onKeyUp` — never pre-warmed, never idling open.
- Audio is streamed to the bot and passed to OpenAI; it is **never written to disk** on the client or the server. The transcript is returned to the key and discarded — not logged (a transcript is user speech).
- `OPENAI_API_KEY` stays server-side; the plugin never sees it.
- Cost guard: 15 s client cap plus the 413 server cap bound per-request spend; the existing per-token rate limiter bounds request frequency.

## Testing

- **Bot (pytest):** `parse_intent` table-driven across every grammar row, including `play playlist X` vs `play X`, normalized playlist-name matching, verb-dependent placement, and the bare-text fallback; route tests with a faked transcription client covering auth, 409-before-transcription, 413, 422, 502, and each dispatch path; dispatcher tests for playlist insert ordering and `stop` being absent.
- **Plugin (vitest):** `buildFfmpegArgs` (device quoting, sample rate, channel count), and the recorder state machine with a faked spawn — start, first-bytes callback, stop, 15 s cap, and the too-short-hold guard. No real microphone in tests.
- **Manual:** each grammar row spoken aloud against a live session; hold-too-short; no-session; a playlist by voice; an unknown playlist; ffmpeg removed from PATH.

Note for the plan: `test_all_control_routes_require_auth` asserts an exact
count of guarded `/control/*` paths and must be bumped (10 → 11). Because this
route registers only when `OPENAI_API_KEY` is set, the test fixture must
register it explicitly — otherwise the sweep silently counts 10 and the new
route goes unverified for auth.

## Out of scope

Wake-word / always-listening (push-to-talk is the privacy boundary), multi-language, `stop`/summon/dashboard by voice, streaming partial transcripts, speaker identification, and any reuse of `discord-ext-voice-recv`.
