# Post-to-Discord Key — Design

**Date:** 2026-08-11
**Status:** Approved
**Scope:** A generic plugin→bot→Discord channel: one new `POST /control/announce` endpoint with a closed command allowlist, and one new configurable "Post to Discord" Stream Deck key. Touches `services/bot/` and `streamdeck-plugin/`.

## Problem

Discord posting from the Stream Deck exists only inside the voice path — `session_info` and `now_playing` are voice-dispatched actions, so the only way to post the session code from the deck is to *speak* it. There is no plain key for it, and no generic path for a key to trigger a `j!`-style posting command.

The ask: plugin actions communicate through the bot to post messages — the deck equivalent of typing `j!session`.

## Decisions

| Question | Decision |
|---|---|
| Shape | **One configurable key**, not one action per command. A "Post to Discord" key with a per-key dropdown, so two keys can post different things and a future command is a dropdown entry, not a new action + icon + route. |
| Endpoint | `POST /control/announce`, body `{"command": "<name>"}`, guarded like every other route. A **closed allowlist**, not a command runner. |
| Allowlist at launch | `session`, `nowplaying`, `queue`, `status`. |
| Output parity | Each command posts **the same embed its `j!` twin posts**, by calling the same builder — never a reimplementation. `session`/`nowplaying`/`queue` already have pure builders in `embeds.py`; `status` builds inline in its cog, so that construction is **extracted** into a shared builder and the cog calls the extraction. Same-output by construction, like the dashboard URL before it. |
| History | One row per post, logged under the `j!` name with `source="streamdeck"` — renders like a typed command, and the per-source dedupe keeps deck presses from merging into and relabelling typed rows. Same choice the Shuffle key made. |
| Cooldown | 10 s per guild, same rationale as the voice announces: a mashed key spams a channel other people read. Within the window the endpoint answers a distinct **429** and the key shows **"Just posted"** rather than a generic failure. |
| Cooldown independence | It is a **separate** window from the voice announce cooldown. Sharing would make the announce key depend on `voice_dispatcher`, which is `None` when `OPENAI_API_KEY` is unset — and a posting key must work on a bot with voice off. Two independent 10 s windows on the same channel is accepted and documented. |
| Empty content | `nowplaying` with nothing playing and `queue` with an empty queue **fail on the key and post nothing** — the person who asked is at the deck, not reading Discord. (Note `queue_embed` itself renders "Queue is empty."; the route checks the queue *before* building, because the j! command answers a person in the channel while the key answers a person at the deck.) `status` always has content and always posts. |
| Mutations | **None reachable.** This endpoint only posts. `play`/`skip`/`volume` have their own routes; free-text messages are excluded — the deck must not become a chat client wearing the bot's name. |

## Components

### 1. Bot — `POST /control/announce`

- Resolves the caller's active session as `skip` does; 409 `no-active-session` without one.
- Body validation: missing/unknown `command` → 400.
- Per-guild 10 s cooldown → 429 `{"error": "just-posted"}`.
- Command dispatch table → embed builder + empty-content check:
  - `session`: `session_embed(code, web_app_url)`; no `sessionCode` → failure, nothing posted.
  - `nowplaying`: `now_playing_embed(currentTrack)`; nothing playing → failure.
  - `queue`: `queue_embed(queue, currentTrack, page=0)`; empty queue → failure.
  - `status`: the extracted `build_status_embed(...)`; always posts.
- Posts via `ChannelNotifier.send(guild_id, embed=...)`; a `False` return (no text channel) is a failed post, reported honestly.
- Logs the history row only after a successful post.

### 2. Bot — status embed extraction

`commands/status.py`'s inline embed construction moves to a builder the cog and the route both call. The guardian fetch (`_guardian_status`) becomes a module function. Uptime (`started_at`) stays on the cog; the route reads it via `bot.get_cog("Status")`, falling back to omitting the uptime line if the cog is somehow absent rather than failing the post.

### 3. Plugin — "Post to Discord" key

- New action `com.jacobchoi.jacky-control.announce`, single state, icon in the existing idiom (72×72, `#1a1a2e` rounded rect, `#e94560` glyph — a speech bubble reads well next to the existing set).
- Per-key setting `command`, chosen in a PI dropdown (Session code / Now playing / Queue / Status), shown by the same UUID-matching mechanism the Summon and Playlist sections use. No default — an unconfigured key flashes ⚠, like an unconfigured Summon key.
- Press → `announce(command)` on the API client → `showOk`; 429 → title "Just posted"; other failures → `showAlert`.

## Error handling

| Condition | Result |
|---|---|
| No live session | 409; key flashes ⚠ |
| Key not configured | No request; key flashes ⚠ |
| Unknown command in body | 400; key flashes ⚠ (cannot happen from our PI, guards a stale plugin) |
| Cooldown window | 429; key shows "Just posted" |
| Nothing playing / empty queue | Failure on the key; nothing posted |
| No stored text channel, or a stale id | **Falls back to the bot's voice-channel text chat** — the session's location. Deck/web-born sessions have no invoking text channel, and this is where they announce. |
| No destination at all (not even voice) | Failure on the key; no history row |
| Bot with voice disabled | Works — no dependency on the voice dispatcher |

## Testing

- **Route:** each command posts its builder's embed via a fake notifier; the history row carries the `j!` name and `source="streamdeck"` and only appears after a successful post; 409/400/429 paths; the cooldown blocks the second post and not the first; empty-content failures post nothing; the endpoint appears in the auth sweep (count +1, updated deliberately).
- **Status parity:** the cog and the route produce the same embed given the same inputs — pinned so the extraction cannot drift.
- **Plugin:** `announce()` posts the right body; unconfigured key makes no request; 429 renders "Just posted"; other errors render alert.
- **Manual:** two keys configured as Session code and Now playing; both post the same embeds `j!session` / `j!nowplaying` produce; mashing a key yields one post and "Just posted".

## Out of scope

Free-text messages, mutation commands through this endpoint, per-command cooldown tuning, and voice-vocabulary changes (`queue`/`status` as *voice* announce verbs can ride the same builders later if wanted).
