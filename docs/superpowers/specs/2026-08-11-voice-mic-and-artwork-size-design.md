# Voice Capture and Artwork Size — Root-Cause Fixes — Design

**Date:** 2026-08-11
**Status:** Approved
**Scope:** Fixes two reproduced defects: voice capture producing zero bytes when no microphone is configured, and Play/Pause artwork being sent at a size a key cannot render. Touches `streamdeck-plugin/` and one error path in `services/bot/`.

## Problem 1: every voice command fails with "Didn't catch that", and no debug echo appears

**Reproduced on the user's machine.** `buildFfmpegArgs` falls back to `audio=default` when no microphone is configured:

```
ffmpeg -f dshow -i "audio=default"
  Could not find audio only device with name [default] among source devices of type audio.
  Error opening input file audio=default.
```

There is **no DirectShow device named "default"** on Windows — dshow requires a real device name. So ffmpeg spawns successfully, immediately errors, and exits having written nothing.

The consequences chain exactly onto the three reported symptoms:

1. Zero bytes captured → the plugin POSTs an empty body.
2. The route's `if not audio: return 422 {"error": "no-speech"}` fires **before transcription** and **before the debug echo**.
3. The plugin maps any 422 to "Didn't catch that".

Confirmed against the live bot log: `POST /control/voice?debug=1 → 422`, with **no** transcription, "resolved by", or debug-echo-failure lines anywhere. Nothing downstream of the audio check ever ran.

Two design faults made this silent rather than obvious:

- **`spawnFailed` only catches ENOENT.** ffmpeg *starting* and then *failing* is indistinguishable from a short press — both end with zero bytes. A process that exits non-zero is not the same as a user who tapped the key.
- **"no audio captured" and "nothing recognised" share a status code**, so the key blames the user's speech for what is a configuration problem.

## Problem 2: the artwork is applied but never appears

**The plugin logs prove it is doing everything right:**

```
INFO artwork: appeared with showArtwork=true
INFO artwork: thumbnail url is now https://i.ytimg.com/vi/…/maxresdefault.jpg
INFO artwork: applying artwork (258023 chars encoded)
```

Settings arrive, the fetch succeeds, `setImage` is called. The number is the fault: **258,023 encoded characters** — a 1280×720 `maxresdefault.jpg`, base64'd, wrapped in an SVG, pushed over the Stream Deck websocket to render on a **72-pixel key**.

The previous investigation ruled out the settings, the state semantics, and the loader; what it could not see was the payload size, because the tests mock the network and the SDK. `mqdefault.jpg` (320×180) is ~10 KB — roughly **25× smaller** and still four times the key's resolution.

## Decisions

| Question | Decision |
|---|---|
| No microphone configured | **Do not invent a device.** Enumerate the real ones and use the first available; if there are none, say so on the key. `audio=default` is removed entirely — it never worked. |
| Detecting a dead capture | Treat a **non-zero ffmpeg exit** as a distinct failure from a short press, and surface it. Capture stderr so the reason is logged. |
| Empty upload | The bot answers a new, distinct code for "you sent no audio" rather than reusing the "nothing recognised" 422, so the key can say **"No audio"** instead of blaming the speech. |
| Artwork size | Rewrite known YouTube thumbnail URLs to a **small variant** before fetching, and cap the encoded payload. A 72-pixel key never needs 1280×720. |
| Non-YouTube artwork | Left alone but still size-capped — an unknown host may serve anything, and the cap is what makes that safe. |
| Voice logging | The voice path gets the same `streamDeck.logger` treatment the artwork path got — device chosen, bytes captured, HTTP status. This blind spot is why the original report took two rounds to diagnose. |

## Components

### 1. Plugin — device resolution

`listInputDevices()` already exists for the Property Inspector's microphone dropdown. Reuse it: when the key's `inputDevice` is unset, resolve to the first enumerated audio device at record time and log which one was chosen. When enumeration yields nothing, fail with a clear key message rather than spawning ffmpeg against a name that cannot exist.

### 2. Plugin — honest failure

`MicRecorder` distinguishes three outcomes rather than two:

- **spawn failed** (ffmpeg missing) → "No ffmpeg";
- **exited non-zero / no device** → "Mic error" (with stderr logged);
- **ran but captured almost nothing** → the existing "Hold longer".

### 3. Bot — a distinct code for an empty upload

`if not audio` returns its own error code, distinguishable by the plugin. The 422 "nothing recognised" path is unchanged, and the debug echo continues to fire there.

An empty upload never reaches transcription, so there is nothing to echo — but the key must not claim it misheard something it never received.

### 4. Plugin — thumbnail sizing

Before fetching, rewrite `i.ytimg.com/vi/<id>/<variant>.jpg` to a small variant. Keep the existing 2 MB ceiling as the outer bound, and add a **much smaller practical cap** on the encoded string; over it, the artwork is skipped and the glyph kept, with the size logged.

## Error handling

| Condition | Key shows | Logged |
|---|---|---|
| No mic configured, one available | (records normally) | which device was auto-picked |
| No audio devices at all | "No mic" | enumeration result |
| ffmpeg exits non-zero | "Mic error" | exit code + stderr |
| Zero bytes uploaded | "No audio" | byte count |
| Audio fine, nothing recognised | "Didn't catch that" | unchanged; debug echo still posts |
| Artwork too large after rewrite | glyph | the encoded size |

## Testing

- `buildFfmpegArgs` **never emits `audio=default`** — the regression that caused this.
- A non-zero exit is reported distinctly from a short press.
- Thumbnail URL rewriting: `maxresdefault`/`hqdefault`/`sddefault` → the small variant; a non-YouTube URL is untouched; a rewritten URL still round-trips through the loader.
- An oversized encoded image is skipped rather than sent.
- Bot: an empty upload returns the new code and posts no debug echo; a non-empty one that resolves nothing still returns 422 **and** posts the echo.
- **Manual:** press the key with no mic configured — it should now record using an auto-picked device, and the debug echo should appear in Discord.

## Out of scope

Resampling or re-encoding artwork client-side, mic selection UI changes, and any change to the grammar.
