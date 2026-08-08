# Stream Deck Playlist + Dashboard Keys, Now-Playing Thumbnail — Design

**Date:** 2026-08-08
**Status:** Approved
**Scope:** v3 of the Stream Deck control feature (v1 `2026-08-06-...`, v2 OAuth `2026-08-07-...`, both live in prod). Adds two keys — Playlist and Dashboard — and puts the current track's artwork on the Now Playing key. Touches `services/bot/src/jacky/api/control.py`, `streamdeck-plugin/`, and the runbook. No auth, deploy, or Firestore-contract changes.

## Problem

The deck can control playback but cannot *start* a specific playlist, and jumping to the web dashboard means finding the session code by hand. The Now Playing key shows only text where the artwork is already available in the track data.

## Decisions

| Question | Decision |
|---|---|
| Playlist insert semantics | **Play next**: insert the playlist at the head of the queue and jump to it immediately; the existing queue survives behind it. (Rejected: replace-and-play, destructive; append, inaudible mid-session.) |
| Which guild the playlist key targets | The **configured** guild, like the Summon key — per-key settings `{guildId, playlistName}`. A key therefore means "load Chill in my main server", not "…wherever I happen to be". Requires a live session in that guild (409 otherwise). |
| Starting playback after insert | `service.skip()` when something is playing (reuses the proven TrackEnd → `play_next` path); `service.play_next()` when idle (a skip with nothing playing is a no-op). |
| Dashboard key config | **None.** Resolves the caller's current session at press time. |
| Dashboard with no session | Opens the web app's entry page (`/app`) plus a brief ⚠, so the key always does something useful. |
| Where the dashboard URL is built | **Bot-side**, returned whole. The plugin knows only the control API's address, not `WEB_APP_URL`. The session code is fetched fresh per press and never stored in settings — `begin_session` mints a new one each session and teardown invalidates it. |
| Thumbnail transport | Bot returns the track's `thumbnail` URL; the **plugin** fetches it and sets a data URI. Refetched only when the URL changes, not per poll. |
| Now Playing title | `TitleAlignment` middle → **bottom**, so the scrolling title sits under the artwork. |

## Components

### 1. Bot — three routes in `control.py`

All guarded (bearer → `token_store.resolve` → rate limit), handlers `(request, user_id)`.

| Route | Behavior |
|---|---|
| `GET /control/playlists` | Modeled on `channels` — no live session needed (the PI configures before a session exists). For each guild that is activated *and* has the caller as a member: `{"guildId", "guildName", "playlists": [{"name", "trackCount"}]}`, sorted by name. |
| `POST /control/playlist` | Body `{"guildId", "playlistName"}`. Membership + activation checked like `summon`; requires a live session in that guild (409 `no-active-session`). Loads the playlist, writes `queue = [*playlist_tracks, *existing_queue]`, then starts it (below). Returns `{"inserted": <n>, "playlistName": <name>}`. |
| `GET /control/dashboard-url` | `resolve_guild` → active: `{"active": true, "url": "<web>/dashboard/<code>", "guildName"}`; no session: `{"active": false, "url": "<web>/app"}`. `<web>` is `settings.web_app_url` (already `rstrip("/")`-ed). |

**Insert-and-play, precisely:**

```
if not _is_valid_document_id(name): 400   # "/" ".." "__x__" would reach Firestore
tracks = (await repo.load_playlist(sid, name) or {}).get("tracks") or []
if not tracks: 404 {"error": "no-such-playlist"}
queued = [{**t, "requestedBy": <display name>} for t in tracks]   # matches library.py
existing = await repo.get_queue(sid)
# Decide BEFORE the write — see "Known interaction" below.
was_playing = bool((await repo.get_state(sid) or {}).get("currentTrack"))
await repo.update_state(sid, {"queue": [*queued, *existing]})
if was_playing: await service.skip(guild.id)
else:           await service.play_next(guild.id)
```

`requestedBy` mirrors `commands/library.py` so leaderboard attribution stays
consistent. The value is the **guild member's `display_name`** — the member
object is already resolved for the membership check, so this costs nothing and
matches how `j!` commands attribute (`ctx.author.display_name`). It is not the
token's stored `userName`: `guarded` hands the handler only a `user_id`, and
re-reading the token document for a display string would be a wasted round trip.

**Known interaction (tested, not left to chance):** `state/listener.py` also
auto-starts playback when it sees the queue grow while idle — and the queue
write is precisely what wakes it. So the playing/idle decision is read
**before** the write, leaving zero awaits between the write and the
`skip`/`play_next` call. Any await in that gap is a window for the listener to
fire first and pop the track we just inserted (losing it, or double-advancing).
`test_playlist_decides_before_writing_the_queue` pins the ordering — it was
verified to fail when the read is moved back after the write, so a refactor
cannot silently reintroduce the race. `PlayerService._advancing` is a second
line of defence, not the primary one.

### 2. Bot — thumbnail on now-playing

`now_playing` gains `"thumbnail": current.get("thumbnail") or None`. No other field changes; existing plugin versions ignore the addition.

### 3. Plugin

- **`api-client.ts`:** `playlists(): Promise<PlaylistList>`, `playPlaylist(guildId, playlistName): Promise<{inserted: number}>`, `dashboardUrl(): Promise<{active: boolean; url: string}>`; `NowPlaying`'s active variant gains `thumbnail: string | null`.
- **`src/actions/playlist.ts`** (new, UUID `.playlist`): per-action settings `{guildId?, playlistName?}`; missing settings or no client → `showAlert`; success → `showOk`; any error → `showAlert`.
- **`src/actions/dashboard.ts`** (new, UUID `.dashboard`): no settings; press → `dashboardUrl()` → `streamDeck.system.openUrl(url)`; `active === false` → also `showAlert` (still opens the entry page); request failure → `showAlert` only.
- **`src/thumbnail.ts`** (new): `loadThumbnail(url, fetchFn?)` → data URI, with a 5 s abort timeout and a 2 MB ceiling (a Stream Deck key is 72 px; anything larger is a wrong URL, not artwork). Returns `null` on any failure — the caller falls back to the default icon.
- **`src/actions/now-playing.ts`:** track the last thumbnail URL; on change, fetch and `setImage(dataUri)`; on no-session/offline/no-thumbnail/fetch-failure, `setImage()` with no argument (resets to the manifest image). Title logic unchanged.
- **`pi-bridge.ts`:** new `"get-playlists"` event → `{event: "playlists", data}` / `{event: "playlists-error", error}`.
- **`ui/settings.html`:** a `.playlist`-gated section with two `<sdpi-select>`s (server → playlist), mirroring the existing `.summon` section; the guild select refilters the playlist list on change.
- **Manifest:** Playlist and Dashboard actions (single state each), Now Playing `TitleAlignment` → `bottom`, Version → `0.3.0.0`. New icons `imgs/playlist.svg`, `imgs/dashboard.svg` in the house style (dark rounded square, coral glyph).

## Error handling

- Playlist: 403 not-a-member / not-activated, 409 no-active-session, 404 no-such-playlist, 400 bad-request — all render as the standard ⚠ flash.
- Dashboard: never hard-fails; worst case opens `/app`.
- Thumbnail: any failure (timeout, oversize, non-image, offline) → default icon, no toast, no retry storm (one attempt per URL change).

## Security

No new auth surface — all three routes reuse `guarded`. One new consideration: the plugin now fetches an image URL supplied by the bot. The bot is the user's own trusted server and the URL originates from Lavalink track metadata, but the fetch is bounded (5 s, 2 MB, failures swallowed) and the result is only ever used as a key image, never executed or parsed as markup.

## Testing

- **Bot (pytest):** playlists listing (activated-only, membership-only, empty list → `[]`, trackCount correct); insert ordering (playlist ahead of an existing queue, existing entries preserved); playing branch calls `skip`; idle branch calls `play_next`; unknown/empty playlist → 404; bad body → 400; non-member/not-activated → 403; no session → 409; dashboard-url active and inactive shapes; now-playing carries `thumbnail` (present and null).
- **Plugin (vitest):** the three new client methods (URL, method, headers, body, parsed result); `loadThumbnail` (happy path data URI, oversize rejected, non-2xx → null, timeout → null).
- **Manual:** configure a playlist key, press with a live session (queue jumps), press with nothing playing, press with no session (⚠); dashboard key with and without a session; artwork appears and changes across tracks and clears when the session ends.

## Out of scope

Playlist name/session code rendered on the key face; creating or editing playlists from the deck; Stream Deck+ dials; server-side image resizing.
