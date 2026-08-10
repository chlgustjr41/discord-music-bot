# Voice Debug Echo, and the Play/Pause Artwork Fix — Design

**Date:** 2026-08-10
**Status:** Approved
**Scope:** Adds a per-key "Print debug message to Discord" option to the Voice key, adds runtime logging to the plugin, and makes the Play/Pause artwork self-healing. Touches `services/bot/` and `streamdeck-plugin/`.

## Problem

Three reports.

### 1. "open dashboard" and "session code" do not execute

Investigated before changing anything. Every layer checks out:

| Checked | Result |
|---|---|
| `parse_structured("open the dashboard")` | resolves to `open_dashboard` |
| `parse_structured("session code")` | resolves to `session_info` |
| `POST /control/voice` response | includes the `client` directive array |
| Dispatcher | handles both verbs |
| Plugin | walks `result.client` and calls `openUrl` behind the scheme guard |

So the wiring is correct and the failure is **not reproducible from the code**. What is missing is any way to see what actually happened on a real press — which is exactly what report 2 asks for. The debug echo is therefore the instrument for this, not a separate task.

Two plausible-but-unconfirmed causes worth noting: `session_info` is subject to a **10-second per-guild announce cooldown** (a second attempt inside that window fails with "Just posted"), and `open_dashboard` opens a browser on the machine running the *plugin*, which is easy to miss if a window is already focused elsewhere.

### 2. No visibility into what was heard

There is no way to see the transcript, how it was resolved, or what ran. Every diagnosis is guesswork.

### 3. Play/Pause never shows the artwork

Ruled out with evidence rather than assumed:

- **`setState` clobbering `setImage`** — the SDK's own typings state that with no `state` supplied *"the image is set for both states"*, so switching state does not drop a custom image.
- **`loadThumbnail` failing** — run against real `i.ytimg.com` URLs: 200, `image/jpeg`, 10–65 KB, well inside the 2 MB ceiling. Returns a valid data URI.
- **The PI section never showing** — the reveal is correct (`actionInfo.action.endsWith(".play-pause")`).
- **The unit tests** — 12 pass, including artwork, but they mock the network and the SDK, so they cannot see this.

What remains unproven is runtime settings delivery, or something repainting the key after the image is set. **The plugin's `logs/` directory is empty — it never logs anything**, so there is no evidence to distinguish them.

The fix therefore does two things rather than guessing at one: add logging so the next occurrence is diagnosable, and make the image **idempotent per poll** so any external repaint is corrected within one tick instead of permanently.

## Decisions

| Question | Decision |
|---|---|
| Debug destination | The session's own Discord text channel, via the existing `ChannelNotifier` — the same place `j!nowplaying` posts. No new surface. |
| Debug content | The transcript, **how** it was resolved (grammar or reasoning), the actions with their placements, and each one's result. That middle field is the one that answers "why did it do that". |
| Debug is opt-in | Per-key checkbox, default **off**. It posts to a channel other people can read, so it must never be on by accident. |
| Transport | A `?debug=1` query parameter on `POST /control/voice`, like `language`. The server never turns it on by itself. |
| Artwork repaint | Cache the encoded data URI per key and re-apply it on **every** poll while the option is on. Fetching still happens only on track change; only the cheap `setImage` repeats. |
| Plugin logging | `streamDeck.logger` on the artwork path — settings seen, thumbnail URL, fetch outcome, image applied. Enough to tell "the option is off" from "the fetch failed" from "we set it and something else overwrote it". |

## Components

### 1. Bot — voice debug echo

The `voice` route, after dispatch, when `debug` is set: build a plain-text message and post it through the notifier.

```
🎙️ Heard: "play playlist chill next"
Resolved by: grammar
Actions: playlist(chill, next) → Queued 12
```

Reuses the existing announce path. It is **not** subject to the announce cooldown — the cooldown exists to stop a misrecognition spamming an embed into the channel, whereas the debug echo is explicitly requested per press and is worthless if it silently drops.

**Transcript privacy:** this is the one place transcribed speech is deliberately published to Discord, and it is opt-in per key. It still must never reach container stdout — the existing invariant is unchanged, and the debug path must not log.

### 2. Bot — resolution provenance

The route already knows whether the grammar or the interpreter produced the actions. That fact is currently only used for an INFO log; it becomes a value the debug message can report.

### 3. Plugin — Voice key option

A **Print debug message to Discord** checkbox, default off, passed as `?debug=1`. Sits beside the Language dropdown in the Voice-key section.

### 4. Plugin — artwork self-healing and logging

`KeyState` gains `lastThumbData: string | null` — the encoded SVG, cached. Each poll, with `showArtwork` on:

- if the thumbnail URL changed → fetch, encode, cache, apply;
- if it did not change but a cached image exists → **apply it again**.

Re-applying an unchanged image is a cheap local call and makes the key converge no matter what repainted it. The alternative — trusting that nothing else ever touches the key — is what the current code assumes and is exactly what cannot be verified from here.

## Error handling

| Condition | Result |
|---|---|
| Debug off | Nothing posted; identical behaviour to today |
| Debug on, no text channel | Debug post fails silently; the actions still ran |
| Debug on, nothing recognised | Posts the transcript and "Resolved by: nothing", which is the most useful case of all |
| Artwork option off | No `setImage` call at all, as today |
| Fetch fails | Manifest glyph; the failure is logged |
| Something repaints the key | Corrected on the next poll |

## Testing

- **Route:** `?debug=1` posts exactly one message containing the transcript, the provenance and the verbs; without it, nothing is posted; a debug post failure does not fail the request; the announce cooldown does not suppress it.
- **Provenance:** grammar-resolved reports grammar; interpreter-resolved reports reasoning; unresolved reports neither and still posts when debug is on.
- **Plugin:** `?debug=1` appears only when the setting is on; the cached image is re-applied on a poll where the URL did not change; a changed URL refetches exactly once.
- **Manual:** turn the option on, press the key, and read the channel — that is now the diagnostic for reports 1 and 3.

## Out of scope

A debug view anywhere but Discord, per-server debug defaults, and changing the announce cooldown for non-debug posts.
