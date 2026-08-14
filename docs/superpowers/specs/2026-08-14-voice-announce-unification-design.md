# Voice Inquiries Through the Announce Path — Design

**Date:** 2026-08-14
**Status:** Approved
**Scope:** Voice inquiries — session code, now playing, **queue**, **status** — post to Discord through the same machinery the Post to Discord key uses. One shared announcer replaces the two parallel posting implementations. Touches `services/bot/` only; **no plugin change**.

## Problem

The announce endpoint and the voice dispatcher each carry their own posting code:

- `/control/announce` (the Post key) has the full command table — session, nowplaying, **queue**, **status** — its own `AnnounceCooldown`, and now the location-aware destination fallback via the notifier.
- Voice's `_announce` supports only `session_info` and `now_playing`, with its own private builders call and its own `_last_announce` cooldown.

So a spoken "queue" or "status" inquiry has no voice path at all, and the two implementations of "post an embed with a cooldown" have already drifted once (content-vs-cooldown ordering had to be fixed in both places separately). Two copies of one behaviour is the bug factory this codebase keeps eliminating.

## Decisions

| Question | Decision |
|---|---|
| Architecture | Extract a shared **`Announcer`** — command → content check → embed → cooldown → notifier post. The route and the voice dispatcher both call it. |
| New voice verbs | `queue_info` and `status_info`, joining `session_info` and `now_playing`. The vocabulary stays closed; nothing destructive is added. |
| Status via voice | Previously removed by explicit user decision when it meant relaxing the no-session gate; **now explicitly requested** and it costs nothing — the announce path already requires a live session, and the j!status embed already exists. The reversal is the user's own. |
| Cooldown | **One shared 10 s per-guild window** across voice announces and key announces. The earlier two-window design existed only because sharing would have coupled the key to the voice dispatcher; a standalone `Announcer` constructed in `bot.py` and handed to both removes that coupling, so the windows merge. Two features posting into one channel should share one spam bound. |
| Logging | The `Announcer` does **not** log history. Each caller keeps its own convention — the endpoint logs a `streamdeck` row, the voice route logs a `voice` row with the transcript. One posting path, two attribution conventions, no double rows. |
| Ordering invariants | Preserved exactly as pinned by existing tests: content checks **before** the cooldown; the stamp lands **only on a successful post**. |

## Grammar additions (deterministic, no LLM)

| Spoken | Verb |
|---|---|
| `queue`, `the queue`, `show the queue`, `show queue`, `what's in the queue`, `post the queue` | `queue_info` |
| `status`, `bot status`, `system status`, `health`, `health check` | `status_info` |

Bare `queue` is currently **unresolved** (the `queue ` play-prefix requires an argument), so claiming it introduces no conflict — verify that against the grammar rather than assuming, since `queue playlist X` and `queue X` must keep working. The LLM classifier gains the verbs automatically through the schema enum; its prompt gets one line naming the four inquiries so it classifies "can you show everyone the queue" correctly.

## Components

1. **`jacky/announce.py` (new)** — `Announcer(service, bot)` with `post(guild_id, command) -> (ok, detail)`:
   - `session` → `session_embed` (needs `sessionCode`), `nowplaying` → `now_playing_embed` (needs `currentTrack`), `queue` → `queue_embed` (needs non-empty queue), `status` → `build_status_embed` (always posts; gathers node/latency/guardian/uptime as the route does today).
   - Content check → cooldown check → `notifier.send(embed=)` → stamp. Injectable `now`.
2. **`api/control.py`** — the announce handler shrinks to: resolve session → allowlist/400 → `announcer.post` → map `(ok, detail)` onto the existing response contract (429 for the cooldown, 200 `{ok, detail}` otherwise) → history row on success. The old inline table and `AnnounceCooldown` are deleted.
3. **`voice_control.py`** — `_announce` shrinks to a call into the same `Announcer` for all four verbs; `_last_announce`/`_announce_allowed` are deleted. `queue_info`/`status_info` dispatch there too.
4. **`voice_actions.py`** — two verbs added to `_VERBS`; both argument-free; strict-mode schema rules hold automatically (enum derives from `_VERBS`).
5. **`control.py` `_LOG_COMMAND_FOR`** — `queue_info` → `queue`, `status_info` → `status`, so dashboard history renders the `j!` names.
6. **`core/bot.py`** — constructs the `Announcer` once and hands it to both `register_control_routes` and `VoiceIntentDispatcher`. Voice-off bots (`OPENAI_API_KEY` unset) still get a working announce key: the announcer must not live behind the voice guard.

## Error handling

Unchanged shapes, now from one implementation: empty content fails on the key/voice result with the specific reason and posts nothing; cooldown reports "Just posted"; a notifier `False` is an honest failure with no stamp and no history; destination resolution (text channel → bot's voice-channel chat) is the notifier's job and already ships.

## Testing

- **Announcer (new, direct):** all four commands; content-before-cooldown ordering; stamp-only-on-success; the shared window blocks a key post after a voice post and vice versa — the one behaviour that is genuinely new.
- **Route:** existing announce tests pass with at most mechanical fixture changes (the response contract is unchanged); the deleted inline table must leave no dead code.
- **Voice:** `queue_info`/`status_info` dispatch and post; existing `session_info`/`now_playing` tests keep passing; grammar tests for every new phrase, plus regression pins that `queue X` and `queue playlist X` still parse as play/playlist actions.
- **Wiring:** the announcer reaches both consumers; a voice-off bot still wires it to the route.
- **Manual:** say "queue" → the j!queue embed appears where the bot is; say "status" → the health embed; press the Post key within 10 s of a voice announce → "Just posted" (the shared window, observable).

## Out of scope

Plugin changes (none needed — the voice key renders `detail` and the Post key's contract is untouched), announcing anywhere but the notifier's destination, and new commands beyond the four.
