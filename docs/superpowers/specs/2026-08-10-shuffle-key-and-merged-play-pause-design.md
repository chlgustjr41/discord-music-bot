# Shuffle Key, and Now Playing Merged Into Play/Pause — Design

**Date:** 2026-08-10
**Status:** Approved
**Scope:** Adds a Shuffle key to the Stream Deck plugin, and folds the Now Playing key's display into the Play/Pause key as per-key options. Touches `services/bot/` (one new route) and `streamdeck-plugin/`.

## Problem

Two unrelated asks.

1. **Shuffling the queue needs a key.** Voice can already shuffle (`shuffle` is in the voice vocabulary) and so can the dashboard, but there is no Stream Deck key for it.
2. **Now Playing and Play/Pause are two keys doing adjacent jobs.** Now Playing shows the scrolling title and the artwork but does nothing when pressed; Play/Pause toggles playback but shows only a static glyph. On a deck where space is the scarce resource, that is one key's worth of function spread across two.

## Decisions

| Question | Decision |
|---|---|
| Shuffle endpoint | `POST /control/shuffle`, alongside the existing `skip`/`stop`/`volume` routes, guarded identically. Reuses `repo.shuffle_queue`, which the voice dispatcher already calls, so the two paths cannot diverge. |
| Merged key defaults | Both new options **default off**, so an existing Play/Pause key behaves exactly as it does today. The merge adds capability; it does not change a key nobody reconfigured. |
| Title | Per-key `showTitle`. When on, the key marquees the current track exactly as Now Playing does — the same `marquee()` helper and the same 400 ms scroll clock, because a title that advances once per 5 s poll does not read as scrolling. |
| Artwork | Per-key `showArtwork`. When on, the key shows the track thumbnail **instead of** the play/pause glyph. |
| Artwork sizing | `letterboxSvg` unchanged: a 144×144 SVG with `preserveAspectRatio="xMidYMid meet"` over a solid background. That is precisely "keep the original dimensions, fit inside the key" — a 16:9 cover is letterboxed, never stretched or cropped. |
| Falling back | With `showArtwork` on but no artwork (or no session), the key calls `setImage()` with no argument, which restores the manifest image for the current state. A stale cover must never outlive its track. |
| Now Playing key | **Removed.** "Merged" means one key does both jobs; leaving the old one in place would be two keys again. |

### The consequence of removing Now Playing, stated up front

An existing Now Playing key on a deck refers to an action UUID that no longer exists, and Stream Deck will show it as an unknown/blank key after the update. The fix is to drop a Play/Pause key in its place and switch on both options — which is the same key, with press-to-toggle added. This is a personal-install plugin, so there is no Marketplace deprecation path to honour; the alternative is carrying a duplicate action forever.

## Components

### Bot — `POST /control/shuffle`

Resolves the caller's active session exactly as `skip` does, calls `repo.shuffle_queue(sid)`, logs a `shuffle` row to the command history so the dashboard shows it like any other action, and returns `{"ok": true, "count": n}`. `409 no-active-session` when the caller is not in a live session, unchanged from its neighbours.

### Plugin — Shuffle key

`src/actions/shuffle.ts` plus `shuffle()` on the API client, a manifest entry, and `imgs/shuffle.svg` in the existing icon idiom (72×72, `#1a1a2e` rounded background, `#e94560` glyph). Press shuffles and flashes OK, or flashes alert on failure — the same shape as Skip.

### Plugin — Play/Pause gains a display

```ts
type PlayPauseSettings = { showTitle?: boolean; showArtwork?: boolean };
```

Settings are **per key**, so two Play/Pause keys can be configured differently. `SingletonAction` shares one instance across every key using the action, so the handler cannot read "the" settings — it keeps a `Map` from action id to settings, filled on `willAppear` and updated on `didReceiveSettings`, and renders each key from its own entry.

State handling is unchanged: `setState(0|1)` still tracks playing/paused, so a key showing artwork still knows which glyph to fall back to.

The Property Inspector gains a Play/Pause-only section with two checkboxes, shown by the same UUID-matching mechanism the Summon and Playlist sections already use.

## Error handling

| Condition | Result |
|---|---|
| Shuffle with no live session | 409; key flashes alert |
| Shuffle with an empty queue | Succeeds, `count: 0` — shuffling nothing is not an error |
| `showArtwork` on, track has no thumbnail | Manifest glyph for the current state |
| `showArtwork` on, then turned off | Image reverts on the next poll rather than keeping the last cover |
| `showTitle` off | No title is ever written, so a key configured as a plain button stays a plain button |
| Offline / unauthorized / unconfigured | Same messages the key shows today |

## Testing

- **Bot:** the route shuffles, logs one history row, 409s without a session, and appears in the auth sweep like every other guarded route.
- **Plugin:** `shuffle()` posts to the right path; the key flashes OK on success and alert on failure; with both options off the key writes no title and no image (the regression that matters); `showTitle` marquees and stops on a static message; `showArtwork` sets a letterboxed image and reverts when the track loses its artwork; two keys with different settings render differently from one poll.
- **Manual:** shuffle reorders the queue and shows in Command History; a Play/Pause key with both options on behaves like the old Now Playing key and still toggles on press.

## Out of scope

Un-shuffle, per-key polling intervals, artwork on any other key, and any change to the poll cadence.
