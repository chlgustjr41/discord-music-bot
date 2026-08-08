# Stream Deck Voice Command Key — Design

**Date:** 2026-08-08
**Status:** Approved
**Scope:** A `.voice` push-to-talk key in `streamdeck-plugin/` with a bundled ffmpeg, one new guarded route on the bot's control API, a deterministic intent parser, the `VoiceIntentDispatcher` ported from the shelved `feat/voice-control` branch, voice entries in the dashboard's Command History, and two Now Playing polish fixes. No changes to auth, the tunnel, or the OAuth flow.

## Problem

Queueing a track from the deck means configuring a key per playlist or reaching for Discord. Voice is the natural input for "add this song", but the 2026-07 attempt at voice control was shelved.

**Why that attempt failed, and why this one is different.** The blocker was never speech recognition: `discord-ext-voice-recv` 0.5.2a179 produced corrupt audio — with zero packet loss and contiguous RTP sequence numbers, ~49% of decrypted Opus frames failed to decode and the remainder decoded to garbage, so even a full-vocabulary Vosk recognizer transcribed nothing from 23 s of clear speech. The failure lived in the **audio acquisition** layer (an alpha library chasing Discord's changing voice encryption); the intent and playback layers above it were correct and tested.

This design **removes that layer from the architecture** rather than working around it. Audio comes from the user's own microphone via the Stream Deck host. Discord voice receive, the second bot user, the wake phrase, the VAD gate, and the passive/active grammar state machine all disappear — push-to-talk delimits the utterance. A direct mic feed is also cleaner than audio round-tripped through Discord's Opus encoding, so accuracy should exceed what the original design could have reached.

**Verified before committing to this design** (the explicit lesson from the failure — instrument the real acquisition path early, never validate with clean sample files): ffmpeg enumerates the mics, and Node spawns it and receives a valid 16 kHz mono WAV on stdout with a clean exit. Measured: **~1–1.8 s of DirectShow device warmup** before audio flows, which the key's feedback design accounts for.

## Decisions

| Question | Decision |
|---|---|
| Audio capture | ffmpeg (`-f dshow`) spawned from the plugin's Node runtime; WAV on stdout, 16 kHz mono. |
| ffmpeg delivery | **Bundled**, so the plugin is complete with no user setup. A `npm run fetch-ffmpeg` script downloads a pinned **LGPL** win64 build, verifies its SHA-256, and extracts `ffmpeg.exe` into `…sdPlugin/bin/` (git-ignored) before `pack`. Adds roughly 30–40 MB to the packaged plugin; the repo stays small. Runtime resolution: bundled binary → `ffmpeg` on PATH → clear key error. LGPL (not GPL) because the binary is redistributed inside the plugin. |
| Microphone selection | Per-key setting `inputDevice`, chosen from a Property Inspector dropdown. The plugin enumerates devices itself via `ffmpeg -list_devices`; the bot is not involved. Empty = system default. |
| Push-to-talk | `onKeyDown` starts capture, `onKeyUp` stops it (`q` on stdin, SIGKILL fallback). Hard cap 15 s. |
| Warmup handling | The key shows "Listening…" only once the first PCM bytes arrive, so the user knows when to speak. The mic is **not** pre-warmed: keeping it open continuously would defeat the privacy property that makes push-to-talk acceptable. |
| Where STT runs | On the bot, via OpenAI `POST /v1/audio/transcriptions`, model `gpt-4o-mini-transcribe` (~$0.003/min). Keeps the API key server-side and costs the 2-vCPU VM nothing. |
| Intent parsing | **Deterministic ordered matcher**, not an LLM. The requirement is structured phrases for consistent behavior; a rules table is predictable, free, instant, and unit-testable. |
| Free-form case | Only song search. Any transcript matching no command becomes a search query. |
| Destructive commands | `stop` is **excluded** from voice — one misrecognition would end the session and clear the queue with no undo, and a dedicated Stop key exists. Summon and dashboard are excluded too (not playback). |
| Playlist placement | The verb decides: `play playlist X` inserts at the front and jumps; `add`/`queue playlist X` appends. |
| Confirmation | None — auto-run, with the recognized text shown on the key. |
| Transcript logging | Voice commands are **logged to the session's Command History** (dashboard), showing both the recognized speech and the executed action. This is a deliberate reversal of the "never log transcripts" default: the operator asked for an audit trail. Consequence to accept: transcripts persist in Firestore under `servers/{id}/commandHistory` and are visible to anyone with the session dashboard. |

## Grammar

Matched case-insensitively after lowercasing, stripping punctuation, and collapsing whitespace. **Order matters** — first match wins; the bare-text fallback is last.

| Pattern | Intent | Argument |
|---|---|---|
| `skip` \| `next` \| `skip track` | `skip` | — |
| `pause` | `pause` | — |
| `resume` \| `unpause` \| `continue` | `resume` | — |
| `volume up` \| `louder` \| `turn it up` | `volume_up` | — |
| `volume down` \| `quieter` \| `turn it down` | `volume_down` | — |
| `play playlist <name>` | `playlist_play` | `<name>` |
| `(add\|queue) playlist <name>` | `playlist_add` | `<name>` |
| `(play\|add\|queue) <query>` | `search` | `<query>` |
| *anything else, non-empty* | `search` | the whole transcript |

`playlist` is a keyword: "play playlist chill" loads the saved playlist; "play chill" searches. Names match saved playlists by a normalized comparison (lowercase, punctuation and spaces removed), so "chill vibes" finds "Chill Vibes". A playlist command matching nothing returns `no-such-playlist` — it does **not** fall back to a song search, because the user explicitly said "playlist".

Accepted trade-off of the bare-text fallback: a badly misheard command ("pause" → "paws") becomes a search rather than an error. Recoverable with `skip`.

## Components

### 1. Bot — `POST /control/voice`

Guarded like every control route (bearer → TokenStore → rate limit), handler `(request, user_id)`.

- **Request:** raw WAV body, `Content-Type: audio/wav`. 413 above `VOICE_MAX_BYTES` (600 KB ≈ 18 s at 16 kHz mono, above the client's 15 s cap).
- **Session:** `resolve_guild(user_id)` — the caller's live session, like the other action keys. No session → 409, **before** transcription (never pay for a request that cannot succeed).
- **Transcription:** `jacky/api/transcribe.py`, a thin injectable client posting multipart to OpenAI. Failure → 502 `stt-failed`; empty transcript → 422 `no-speech`.
- **Dispatch:** `parse_intent` → `VoiceIntentDispatcher` → log to Command History.
- **Response 200:** `{"transcript", "intent", "ok", "detail"}` — `detail` is what the key displays (queued track title, or "No playlist called X").

### 2. Bot — `jacky/api/voice_intent.py` (new, pure)

`parse_intent(transcript) -> Intent(kind, arg)`. No I/O; the grammar table lives here and is the primary test target.

### 3. Bot — `jacky/voice_control.py` (ported)

`VoiceIntentDispatcher` from `feat/voice-control`, `stop` removed, two playlist intents added. Both follow the ordering proven in `play_playlist`: decide from a state read **before** the queue write, with no await between the write and the start call, so the Firestore listener cannot pop the track just inserted.

- `playlist_play` — tracks to the **front**; then `skip()` if something is playing, else `play_next()`.
- `playlist_add` — tracks to the **end**; starts playback only if nothing is playing. Appending must never interrupt the current track.

### 4. Bot — Command History logging

Voice commands are logged through the existing `commandHistory` subcollection so the dashboard picks them up with no new query, using the **executed action** as `command`/`args` (so the dashboard's existing retrigger keeps working) plus two new fields:

| Field | Value |
|---|---|
| `command` | executed action — `play`, `skip`, `pause`, `resume`, `volume`, `playlist` |
| `args` | the executed argument (query / playlist name), or `""` |
| `source` | `"voice"` (absent on existing Discord/web entries → treated as `"discord"`) |
| `transcript` | the recognized speech |

**Required repository change:** `_log_command` currently de-duplicates on `(command, args)` and increments `callCount`. Without including `source`, a voice `play X` would merge into an existing Discord `play X` entry and silently relabel it as voice. The dedupe query must include `source`, so Discord and voice entries stay distinct rows.

### 5. Frontend — Command History rendering

`CommandHistoryEntry` gains `source?: string` and `transcript?: string`. Entries with `source === "voice"` render with a **mic badge** and show the recognized speech alongside the executed action, instead of the `j!{command} {args}` form (which would be wrong — voice commands were never typed). Existing entries are unaffected: absent `source` renders exactly as today.

### 6. Plugin

- **`src/ffmpeg-path.ts` (new):** resolve bundled `bin/ffmpeg.exe` → PATH → null.
- **`src/audio-capture.ts` (new):** `buildFfmpegArgs(device)` (pure, testable) and a `MicRecorder` wrapping spawn/collect/stop with the 15 s cap and a first-bytes callback driving the "Listening…" transition.
- **`src/actions/voice.ts` (new):** UUID `.voice`. `onKeyDown` records; `onKeyUp` stops, POSTs, renders the result. Hold shorter than warmup or zero bytes → ⚠ "hold longer".
- **`api-client.ts`:** `voiceCommand(wav: Uint8Array)`.
- **`pi-bridge.ts` / PI:** `get-audio-devices` → plugin runs `ffmpeg -list_devices` → dropdown bound to the per-key `inputDevice`.
- **Manifest:** `.voice` action, Version → `0.4.0.0`, new `imgs/voice.svg`.
- **`scripts/fetch-ffmpeg.mjs` (new):** downloads the pinned LGPL build, verifies SHA-256, extracts `ffmpeg.exe` into `…sdPlugin/bin/`. `bin/ffmpeg.exe` is git-ignored. The exact release URL and hash are resolved and recorded during implementation — never guessed.

### 7. Plugin — Now Playing polish (independent of voice; could ship separately)

- **Smooth title scroll.** Today the marquee advances only on the 5 s poll tick, so long titles crawl. A local timer advances the offset every ~400 ms, independent of polling, and runs only while a title is longer than the display width. It stops when the key is hidden or the title fits, and resets on track change.
- **Correct thumbnail aspect ratio.** Artwork is 16:9 while a key is square, so passing the raw JPEG distorts it. `setImage` accepts an **SVG string**, so the JPEG is embedded in a square SVG with `preserveAspectRatio="xMidYMid meet"` over a dark background — letterboxing it at the correct ratio with no image library and no native dependency.

### 8. Config & deploy

`OPENAI_API_KEY` (already in `deploy/.env`) and optional `OPENAI_STT_MODEL` (default `gpt-4o-mini-transcribe`), passed through in `docker-compose.yml`. The route registers only when the key is present — the same graceful-disable pattern as the OAuth gate.

## Error handling

| Condition | Result |
|---|---|
| ffmpeg missing (bundle and PATH) | Key shows "No ffmpeg" + ⚠; nothing sent |
| Device invalid / capture fails | "Mic error" + ⚠ |
| Hold too short / no audio | "Hold longer" + ⚠ |
| No live session | 409 → ⚠ |
| Body too large | 413 → ⚠ |
| Transcription failure | 502 → "STT failed" |
| Empty transcript | 422 → "Didn't catch that" |
| Unknown playlist | 200 `ok:false` → "No playlist called X" |
| Search found nothing | 200 `ok:false` → "No results" |

Every failure is visible on the key and leaves playback untouched.

## Security & privacy

- No new exposure: one more route behind the same bearer token, rate limiter, and tunnel.
- The microphone opens only while the key is held and is released on `onKeyUp` — never pre-warmed, never idling open.
- Audio is streamed to the bot and passed to OpenAI; it is **never written to disk** on client or server.
- Transcripts **are** persisted, by explicit request, to `servers/{id}/commandHistory` and shown in the dashboard. Anyone with the session dashboard can read them. Audio itself is still never stored.
- `OPENAI_API_KEY` stays server-side; the plugin never sees it.
- The bundled ffmpeg is pinned by SHA-256 and verified at fetch time, so a compromised or swapped upstream artifact fails the build rather than shipping silently.
- Cost guard: 15 s client cap plus the 413 server cap bound per-request spend; the per-token rate limiter bounds frequency.

## Testing

- **Bot (pytest):** `parse_intent` table-driven across every grammar row, including `play playlist X` vs `play X`, normalized name matching, verb-dependent placement, and the bare-text fallback; route tests with a faked transcription client for auth, 409-before-transcription, 413, 422, 502, and each dispatch path; dispatcher tests for both playlist placements and `stop` absence; a repository test proving voice and Discord entries with the same `(command, args)` stay separate rows.
- **Plugin (vitest):** `buildFfmpegArgs` (device quoting, rate, channels), ffmpeg path resolution order, the recorder state machine with a faked spawn (start, first-bytes, stop, 15 s cap, too-short guard), the marquee timer, and the SVG letterbox builder. No real microphone in tests.
- **Frontend:** no test harness exists; verified visually — a voice entry shows the mic badge and both fields, and existing entries are unchanged.
- **Manual:** every grammar row spoken against a live session; hold-too-short; no-session; playlist by voice; unknown playlist; ffmpeg removed from PATH *and* bundle (expect "No ffmpeg"); a long title scrolling smoothly; a 16:9 thumbnail rendering undistorted.

Note for the plan: `test_all_control_routes_require_auth` asserts an exact count of guarded `/control/*` paths and must be bumped (10 → 11). Because this route registers only when `OPENAI_API_KEY` is set, the test fixture must register it explicitly — otherwise the sweep silently counts 10 and the new route goes unverified for auth.

## Out of scope

Wake-word / always-listening, multi-language, `stop`/summon/dashboard by voice, streaming partial transcripts, speaker identification, macOS/Linux ffmpeg bundles (Windows-only, matching the manifest's existing `OS` entry), and any reuse of `discord-ext-voice-recv`.
