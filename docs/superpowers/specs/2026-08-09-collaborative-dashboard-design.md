# Collaborative Session Dashboard — Design

**Date:** 2026-08-09
**Status:** Approved
**Scope:** Signed-in presence, live cursors, and an opt-in shared view on the session dashboard. Touches `frontend/` and `firestore.rules` only. No bot, plugin, or deploy-contract changes.

## Problem

The session dashboard is already multi-user — everyone with the code sees the same queue, playback, and search results, because they all read one Firestore document. But nothing tells you *who else is here*. There is no signed-in indicator on the dashboard, no list of active viewers, and no sense of what anyone else is doing. Two people can fight over the search box without ever knowing the other exists.

Four things are wanted:

1. A signed-in account indicator on the dashboard, matching the landing page.
2. A Google-Docs-style row of who is currently viewing this session.
3. Other people's mouse positions, live.
4. Shared view as an opt-in for signed-in users; anonymous visitors stay entirely local.

## What already exists (verified, not assumed)

- **Landing page account icon** — `AccountMenu` is already rendered in `LandingPage.tsx`. **No change needed.**
- **Idle auto sign-out** — `useIdleSignOut()` is already called app-wide in `App.tsx`: 30-minute limit, warning toast 60 s before, cross-tab via `localStorage`, timestamp-based so a sleeping laptop signs out on wake. **No change needed.**
- **Shared search** — `SearchPanel` writes `searchQuery` to `servers/{id}` and the **bot** performs the search and writes `searchResults` back. Search is therefore already shared, and is a *bot capability* rather than a client one.

Items 1 and 2 of the request are already satisfied; this spec verifies them with tests rather than rebuilding them.

## Decisions

| Question | Decision |
|---|---|
| Transport | **Firestore**, not RTDB. RTDB would give `onDisconnect` for free, but is not provisioned and would need console work. Firestore needs no new infrastructure and the rules are in this repo. |
| Where presence lives | A **new top-level `presence/{sessionCode}/participants/{uid}`** collection — deliberately *not* under `servers/`, whose rules are `allow read, write: if true` for every subcollection. A new top-level path is the only way to get real rules. |
| Liveness | Heartbeat every 15 s; clients ignore entries older than **45 s**. Firestore has no server-side disconnect hook, so staleness filtering — not `onDisconnect` — is what removes ghosts. Best-effort delete on unload as well. |
| Cursor transport | The same participant document, `cursor: {x, y} \| null`. No second collection: a cursor without presence is meaningless. |
| Cursor rate | Throttled to **100 ms**, and only when the pointer moved **≥ 8 px**, and only while the tab is visible. Bursty ~3 writes/s/user in practice. |
| Coordinate space | Normalized `0..1` against the dashboard content element, so a 1080p and a laptop viewport put the cursor in the proportionally same place. Values outside `0..1` are dropped rather than clamped — they mean the pointer left the shared area. |
| Who participates | **Signed-in users only.** Anonymous visitors never write a presence doc and never appear to others. |
| Mode | `shared` (default for signed-in) or `solo`, stored per session in `localStorage`. Anonymous users are always solo and the toggle is not offered. |
| Solo search | Tries a **client-side** search first; falls back to the bot path when that endpoint is unavailable. See below. |

## The solo-search limitation, stated plainly

`functions/searchYouTube` exists in the repo but **is not deployed** (verified: 404 in `us-central1` and `us-east1`), and `VITE_FUNCTIONS_URL` is unset. So today the only working search is the bot path, which writes to the shared document by construction.

Rather than pretend otherwise:

- Solo mode **stops following** the shared search — another user's search no longer replaces your results. That is the half of isolation that is fully deliverable today, and it is the half that actually annoys people.
- Solo mode **attempts a client-side search first** via `services/api.ts` (already written, currently unused) against a same-origin `/api/searchYouTube` hosting rewrite. If that returns non-OK — which it will until the function is deployed — it falls back to the bot path and shows a one-time toast: *"Search runs through the bot, so results are visible to others in this session."*
- The moment `searchYouTube` is deployed with a `YOUTUBE_API_KEY`, solo search becomes fully private with **no frontend change** — the fallback simply stops firing.

Honest limitation, working today, with the upgrade path wired.

## Components

### 1. `src/lib/presence.ts` (new, pure — tested)

No Firebase imports, so it can be tested exhaustively:

- `colorForUid(uid)` — deterministic hue from a stable hash, so a person keeps their colour across reloads and looks the same to everyone.
- `livingParticipants(all, now, ttlMs)` — drops stale entries and the local user.
- `toNormalized(clientX, clientY, rect)` / `toPixels(point, rect)` — coordinate mapping; returns `null` when outside the rect.
- `movedEnough(prev, next, minPx, rect)` — the write-suppression threshold.
- `shouldPublish(mode, signedIn)` — one place that answers "does this browser broadcast?", so the auth gate cannot be half-applied.

### 2. `src/hooks/usePresence.ts` (new)

Subscribes to `presence/{code}/participants`, publishes own doc, heartbeats, deletes on unload and on switching to solo. Returns `{ participants, publishCursor }`. Publishes nothing at all when `shouldPublish` is false.

### 3. `src/components/PresenceBar.tsx` (new)

Overlapping avatar stack with the user's colour as a ring, initials fallback, name on hover, and a `+N` overflow chip past four.

### 4. `src/components/CursorLayer.tsx` (new)

A `pointer-events: none` overlay rendering each remote cursor as an arrow plus a name pill in that person's colour. Positions are interpolated with a short CSS transition so 10 Hz updates read as smooth motion rather than teleporting.

### 5. `src/components/SharedViewToggle.tsx` (new)

Two-state control, signed-in only, with a tooltip explaining what sharing broadcasts.

### 6. `src/components/Dashboard.tsx` (modified)

Header gains `AccountMenu`, `PresenceBar`, and `SharedViewToggle`. The content area becomes the cursor coordinate reference and hosts `CursorLayer`. Passes `shared` down to `SearchPanel`.

### 7. `src/components/SearchPanel.tsx` (modified)

Accepts `shared: boolean`. In shared mode it behaves exactly as today. In solo mode it ignores incoming `searchQuery`/`searchResults` props and runs its own search through `services/api.ts`, falling back as described.

### 8. `firestore.rules` (modified)

```
match /presence/{sessionCode}/participants/{uid} {
  allow read: if request.auth != null;
  allow write: if request.auth != null && request.auth.uid == uid;
}
```

Read requires auth so anonymous visitors cannot enumerate who is in a session. Write is uid-scoped, so nobody can move someone else's cursor or impersonate them — the one place in this app where per-user write rules are both meaningful and achievable.

### 9. `firebase.json` (modified)

A `/api/searchYouTube` rewrite to the `searchYouTube` function, ordered before the SPA catch-all. Same-origin, so no CORS and no env var.

### 10. Test infrastructure (new)

The frontend has **no test runner today**. Vitest + jsdom are added so `presence.ts` and the search-mode decision logic get the same mutation-verified treatment as the bot and the plugin. Only pure logic is tested; components are verified in the browser.

## Security and privacy

- **Presence is signed-in only, both directions.** Anonymous users neither publish nor read — enforced in rules, not just UI.
- **Cursors cannot be spoofed.** The uid-scoped write rule means a client can only move its own cursor.
- **Nothing new is exposed.** A participant doc holds display name, photo URL, colour, and a normalized coordinate — all already visible to anyone in the session, except the coordinate, which is the feature.
- **Leaving is observable.** Solo-mode switch and unload both delete the doc; the 45 s staleness filter is the backstop for a hard crash.
- **Cost is bounded** by the 100 ms throttle, the 8 px threshold, and the visibility check, so a backgrounded tab writes nothing.

## Error handling

| Condition | Result |
|---|---|
| Not signed in | No presence, no cursors, solo only; toggle not rendered |
| Signed in, solo | No doc written; own doc deleted; others' cursors hidden |
| Presence listener fails | Dashboard renders normally without presence — never blocks playback |
| Stale participant (crash, lost network) | Filtered out after 45 s |
| Cursor outside the content rect | Not published; remote cursor hidden |
| Solo search endpoint unavailable | Falls back to the bot path, one-time toast explaining results are shared |
| Session ends while others are present | Existing session-expired path is unchanged |

## Testing

- **`presence.ts`:** colour stability and spread; staleness boundary; self excluded; normalization round-trips; out-of-rect returns null; threshold suppresses sub-8px moves; `shouldPublish` false when signed out **or** solo.
- **Search mode:** shared mode follows incoming props; solo mode ignores them; fallback fires on endpoint failure and only toasts once.
- **Manual (browser):** two sessions side by side — avatars appear and disappear, cursors track, toggling to solo removes the avatar for the other viewer, an anonymous window sees no presence UI at all, and a search in one shared window updates the other but not a solo one.

## Out of scope

Text selection or scroll sync, cursor chat or reactions, presence on any screen other than the dashboard, persisting who visited, RTDB migration, and deploying `searchYouTube` (needs a YouTube API key that is not available here).
