# Playlist-URL Search Toggle — Design

**Date:** 2026-07-07
**Status:** Approved
**Scope:** Frontend only (`frontend/`). No bot, Lavalink, or Firestore-contract changes.

## Problem

When a user pastes a YouTube URL that carries a playlist context —
`https://www.youtube.com/watch?v=FFjDEkZg-54&list=RDFFjDEkZg-54&index=1`
(the format YouTube produces when clicking through a playlist or Mix) — the
dashboard should show the playlist's tracks, and the user should be able to
opt out and search the URL "normally" (exact video + similar tracks) instead.

### What already works (verified 2026-07-07)

The v2 bot (live since 2026-07-04) already handles the playlist half
end-to-end:

- Prod Lavalink returns `loadType: "playlist"` for `watch?v=X&list=RD...`
  Mix URLs (verified via `/v4/loadtracks` against the prod node), with the
  URL's own video as `selectedTrack`.
- `services/bot/src/jacky/state/listener.py::_handle_search` forwards the
  full playlist (linked video first) plus `searchPlaylistName`.
- Both search surfaces already render a playlist banner and (in SearchPanel)
  auto-select all tracks.

What is missing is **user-visible detection before results arrive** and a
**toggle to force normal-URL search**. Because the search protocol is
"frontend writes a query string to `servers/{id}.searchQuery`", the toggle
is implementable purely as a client-side URL rewrite.

## Decisions

| Question | Decision |
|---|---|
| Which URL formats | `watch?v=X&list=Y` style (incl. `youtu.be/<id>?list=Y`). Pure `/playlist?list=` URLs out of scope (not produced by normal YouTube browsing); they pass through unchanged with no toggle. |
| Which surfaces | Both: dashboard `SearchPanel` and playlist manager `PlaylistOnlineSearch`. |
| Toggle semantics | "Normal search" = strip playlist params client-side → existing bot path returns the exact video + ~9 similar tracks. |
| Mechanism | **Approach A — client-side URL rewrite.** No `searchMode` protocol field, no dual result sets. Bot and Firestore contract untouched. |
| Default mode | Playlist mode whenever a playlist URL is detected; resets to playlist mode when the query text changes. |
| Testing | Add vitest (dev-dep) with unit tests for the URL parser. |

## Components

### 1. `frontend/src/lib/youtubeUrl.ts` (new)

Pure helper shared by both surfaces:

```ts
export interface ParsedYouTubeQuery {
  /** true when the query is a YouTube watch URL with both a video id and a list param */
  isPlaylistUrl: boolean;
  /** the same URL with playlist params (list, index, start_radio, pp) removed; null when not a playlist URL */
  videoOnlyUrl: string | null;
}
export function parseYouTubeQuery(query: string): ParsedYouTubeQuery;
```

Rules:

- Hosts: `youtube.com`, `www.youtube.com`, `m.youtube.com`,
  `music.youtube.com`, `youtu.be`.
- Playlist URL ⇔ has `list` query param **and** a video id (`v` param on
  youtube.com hosts; first path segment on `youtu.be`).
- `videoOnlyUrl` preserves the original URL's other params (e.g. `t=`),
  removing only `list`, `index`, `start_radio`, `pp`.
- Non-URLs, unparseable URLs, other hosts, and `list`-without-video URLs →
  `{ isPlaylistUrl: false, videoOnlyUrl: null }`.

### 2. `SearchPanel.tsx` changes

- New state: `mode: "playlist" | "normal"` (default `"playlist"`).
- Query-change handler resets `mode` to `"playlist"`.
- Debounced search effect computes the effective query:
  `mode === "normal" && parsed.videoOnlyUrl ? parsed.videoOnlyUrl : trimmed`.
  The existing `lastSentQuery` dedupe guard makes a toggle flip re-fire the
  search automatically (the effective query string changes). `mode` joins the
  effect deps.
- UI: when `parsed.isPlaylistUrl`, render a chip row beneath the input —
  ListMusic icon + "Playlist link detected" + toggle button labeled
  "Search video only" (playlist mode) / "Show playlist tracks" (normal mode).
- Firestore write, results rendering, playlist banner, timeout handling:
  unchanged.

### 3. `PlaylistOnlineSearch.tsx` changes

- Same `mode` state + reset-on-query-change + effective-query rewrite inside
  its own debounce effect, before calling `onSearch(q)`.
- Same chip-row UI beneath the input.
- `useBotSearch` stays untouched (dumb transport hook).

## Data flow (unchanged protocol)

```
user pastes watch?v=X&list=Y ── parse ──► chip + toggle shown
  playlist mode: searchQuery = original URL ──► bot ──► Lavalink playlist ──► all tracks + banner
  normal mode:   searchQuery = watch?v=X    ──► bot ──► Lavalink track    ──► video + similar
```

## Error handling

- Unparseable input degrades to current behavior (no chip, no rewrite).
- Toggle mid-flight: new effective query goes through the normal debounce /
  `waitingForResults` path; the 15s timeout and error states are unchanged.
- Bot failures already write empty results; UI copy unchanged.

## Testing

- **Unit (new):** vitest dev-dep in `frontend/`, `npm test` script; tests for
  `parseYouTubeQuery` — the three example Mix URLs, `youtu.be/<id>?list=`,
  `music.youtube.com`, plain video URL, plain text, `/playlist?list=` (no
  video), param preservation (`t=`), param stripping (`list`/`index`/
  `start_radio`/`pp`).
- **Manual:** paste each example URL in both surfaces → playlist tracks +
  chip; toggle → exact video + similar tracks; toggle back → playlist again;
  plain text search unaffected.

## Out of scope

- `/playlist?list=` page URLs (no video id).
- SoundCloud sets / Bandcamp albums (already load as playlists via the bot).
- Any bot or deploy changes.
