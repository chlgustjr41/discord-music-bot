# Voice — Announce Actions and the Client Directive — Design

**Date:** 2026-08-09
**Status:** Approved
**Scope:** Adds three verbs to the voice vocabulary: post the current track to Discord, post the session code and dashboard link to Discord, and open the dashboard in the browser. Introduces the first voice action that executes on the **client** rather than the server. Touches `services/bot/` and `streamdeck-plugin/`. No change to transcription, auth, the tunnel, or the deploy contract.

## Problem

Voice covers playback but nothing informational. Three things are wanted by voice:

1. **"What's playing?"** — announced in Discord so everyone in the channel sees it, not just the person at the Stream Deck.
2. **"Post the session code."** — the code and dashboard link, so others can join the web app.
3. **"Open the dashboard."** — open the web dashboard in the speaker's own browser.

A health-check verb was considered and **deliberately dropped**: `j!status` already covers it from inside Discord, and routing it through voice would have meant relaxing the route's no-session gate for one verb.

## What already exists

All three informational views are already built and shared as pure functions in `jacky/commands/embeds.py` — `now_playing_embed`, `session_embed`, `error_embed` — used today by `j!nowplaying` and `j!session`. **Nothing is extracted or reimplemented**; the voice path calls the same builders, so the Discord output is identical whichever way it was triggered.

Likewise `GET /control/dashboard-url` already computes the dashboard URL for the Dashboard key. The voice path reuses that computation through a shared helper rather than duplicating it, so the key and the voice command open the same URL **by construction**.

## Decisions

| Question | Decision |
|---|---|
| New verbs | `now_playing`, `session_info`, `open_dashboard`. The vocabulary stays closed and still has **no deletion verb**. |
| Where announcements go | The session's own `textChannelId`, via `ChannelNotifier` — the same channel `j!nowplaying` and `j!session` already post to. No new audience, no new data exposure. |
| Client-side execution | The response gains a top-level `client` array of directives. `open_dashboard` returns `{"type": "open_url", "url": …}`; the plugin executes it after rendering `detail`. |
| `open_dashboard` behavior | Identical to pressing the Open Dashboard key, because it opens the URL that key's endpoint returns. |
| Trust in the directive | The plugin requires an **`https:` URL** before opening, and applies the same check to the existing Dashboard key. Origin pinning is not achievable here — see Security. |
| `now_playing` with nothing playing | Fails on the key ("Nothing is playing"); posts nothing. The person who asked is at the Stream Deck, not reading Discord. |
| Abuse bound | A **10-second per-guild cooldown** on the two announcing verbs (`now_playing`, `session_info`), since a misrecognition can now produce a publicly visible message. `open_dashboard` is exempt — it posts nothing. |
| Fallback parser | Gains phrases for all three, so an OpenAI outage still reaches them. |

## Components

### 1. `jacky/api/voice_actions.py` — vocabulary (extended)

Three verbs added to `_VERBS` and to `ACTION_SCHEMA`'s enum. No new `Action` fields: all three are argument-free. `validate_actions` needs no change beyond the vocabulary itself — which is the point of having the vocabulary in one place.

The strict-mode schema rules still hold: every property in `required`, `additionalProperties: false`, optionality as a union with null.

### 2. `jacky/voice_control.py` — dispatcher (extended)

`DispatchResult` gains `client: dict | None = None`. Every existing action leaves it `None`, so no existing behavior changes shape.

- `now_playing` — reads state; if `currentTrack` is absent returns `DispatchResult(False, "Nothing is playing")`. Otherwise posts `now_playing_embed(current)` and returns the track title as `detail`.
- `session_info` — reads `sessionCode`; absent returns a failure. Otherwise posts `session_embed(code, web_app_url)` and returns the code as `detail`.
- `open_dashboard` — performs **no server-side effect**. Returns `DispatchResult(True, "Opening dashboard", client={"type": "open_url", "url": …})`.

The dispatcher reaches the notifier as `self.service.notifier`.

The **announce cooldown lives in the dispatcher**, not the route: it is a
property of the two verbs that post, so it belongs where the verbs are
handled, and it must not block the other actions in the same utterance. It is
a 10-second per-guild window keyed on guild id, covering `now_playing` and
`session_info` together — saying "what's playing, and post the code" posts the
first and fails the second, which is the honest outcome rather than a silent
drop.

### 3. `ChannelNotifier.send` — one new parameter

Currently accepts `text` or `track`. Gains `embed`, so a caller can hand it a prebuilt embed. Existing callers are unaffected. The method already fails soft when `textChannelId` is missing or the channel is unresolvable; announcing verbs must surface that as a failed `DispatchResult` rather than silently reporting success, so `send` needs to report whether it actually posted.

### 4. `POST /control/voice` — response gains `client`

```json
{
  "transcript": "...",
  "actions": [{"action": "...", "ok": true, "detail": "..."}],
  "ok": true,
  "detail": "...",
  "client": [{"type": "open_url", "url": "https://…/session/CODE1234"}]
}
```

`client` collects the non-null `DispatchResult.client` values in action order, and is `[]` when there are none. Command history logging is unchanged: one row per action, all sharing the transcript. `_LOG_COMMAND_FOR` maps `now_playing` → `nowplaying` and `session_info` → `session` so the dashboard's history displays a spoken command under the same name as the typed `j!` command it corresponds to; `open_dashboard` logs under its own name because it has no `j!` equivalent. This is a display mapping only — it does not make the row's retrigger button do anything, since `_handle_retrigger` implements only `play`/`skip`/`pause`/`resume`/`loop`/`volume` (a pre-existing gap that `clear` and `playlist` rows already share).

### 5. Plugin

`VoiceResult` gains `client`. `voice.ts` renders `detail` exactly as today, then walks `client` and, for each `open_url` directive whose URL passes the scheme check, calls `streamDeck.system.openUrl`. The check lives in one shared helper used by both `voice.ts` and `dashboard.ts`. Unknown directive types are ignored — forward compatibility, so an older plugin against a newer server degrades rather than breaking.

## Security

**The client directive is the sensitive part of this change.** Until now the voice response only *described* what the server had already done. A directive instead *instructs the plugin to act* — and the action is "open this URL in the user's browser."

**But it introduces no new trust, and it is important to be accurate about that.** The existing Dashboard key already calls `streamDeck.system.openUrl()` on a URL the server supplied, with no validation at all. The directive is the same trust over a different transport, not an escalation.

**Origin pinning is not achievable here, and claiming it would be theatre.** The plugin has `DEFAULT_API_URL` but no web-app origin constant, and `apiUrl` is user-overridable in advanced settings. Any "trusted" web-app origin would either have to be supplied by the same server we are trying to check, or be a hardcoded constant that breaks self-hosters. Neither is a real check.

What *is* worth doing, because it stops a genuine class of attack rather than a hypothetical one:

- **Require an `https:` scheme** before opening. This blocks `javascript:`, `file:`, `data:`, and custom-scheme URLs — which are the difference between "opens an unexpected web page" and "executes something locally or hands off to another installed application."
- **Apply the same check to the existing Dashboard key**, which lacks it today. A guard on the new path only, while the old path stays open, would be worse than useless — it would suggest a protection that isn't there.
- **Ignore unknown directive types** rather than dispatching generically. The directive vocabulary is closed, exactly like the action vocabulary.

The residual risk — a user who has pointed `apiUrl` at a hostile server gets sent to an unexpected https page — is accepted, and is identical to the risk the Dashboard key already carries.

Other properties, unchanged from the existing design:

- **No deletion verb still exists.** The three additions are read-and-announce plus a browser open; none removes anything.
- **No new data exposure.** The session code and now-playing already reach that channel through `j!session` and `j!nowplaying`. Announcing posts only to the session's own text channel.
- **Transcript privacy holds.** These verbs carry no arguments, so there is nothing spoken to leak; the existing rule — transcripts reach Firestore command history and never container stdout — is untouched.
- **Blast radius** is bounded by the closed vocabulary, the 5-action cap, and the new per-guild announce cooldown.

## Error handling

| Condition | Result |
|---|---|
| No live session | Unchanged: 409 before transcription, for every verb |
| `now_playing` with nothing playing | Action fails on the key; nothing posted |
| `session_info` with no session code | Action fails on the key; nothing posted |
| No `textChannelId`, or channel unresolvable | Action fails on the key; the rest of the batch still runs |
| Announce cooldown active | Action fails with a "too soon" detail; other actions unaffected |
| Directive URL is not `https:` | Plugin drops it silently and still renders `detail` |
| Unknown directive type | Ignored by the plugin |

## Testing

- **Vocabulary:** the three verbs validate; the schema enum still contains no deletion-shaped verb; strict-mode rules still hold.
- **Dispatcher:** each verb posts the expected embed via a fake notifier; `now_playing` and `session_info` fail without a track / code and post nothing; `open_dashboard` produces a `client` directive and performs no server-side effect; a notifier that cannot post yields a failed result rather than a successful one.
- **Route:** `client` collects directives in action order and is `[]` otherwise; history rows still one-per-action with the transcript; announce cooldown blocks the second call and not the first.
- **Plugin:** an `https:` directive opens the URL; `javascript:`, `file:` and `http:` ones do **not**; an unknown directive type is ignored; `detail` still renders in every case. The Dashboard key gets the same scheme test.
- **Fallback parser:** the three new phrasings map to the right verbs.
- **Manual:** "what's playing" posts to the channel; "post the session code" posts the code and link; "open the dashboard" opens the same page the Dashboard key opens; a two-command utterance mixing an announce and a playback action.

## Out of scope

A voice health check (dropped — `j!status` covers it), announcing to a channel other than the session's own, queue listings by voice, and any change to transcription, auth, or the key lifecycle.
