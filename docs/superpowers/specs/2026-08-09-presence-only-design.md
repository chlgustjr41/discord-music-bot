# Dashboard Presence — Simplification — Design

**Date:** 2026-08-09
**Status:** Approved
**Scope:** Removes live cursors, shared-view sync (panel sections and text inputs), and the shared/solo toggle. Keeps presence alone: who is looking at this session dashboard, shown as account icons. Adds focus state, nickname editing, and hover names. Touches `frontend/` and `firestore.rules`.

## Problem

The collaborative dashboard grew three features in a day — presence, live cursors, and shared view state. In use, only the first earns its keep. Cursors and synced panels/inputs are noise on a screen whose job is controlling music, and the shared/solo toggle only exists to switch that noise off.

What is actually wanted is small: **an avatar row showing who is on this dashboard right now**, greyed when they are not looking at it, with a name on hover, and the ability to set your own nickname.

## What is removed

| Removed | Why |
|---|---|
| Live cursors (`CursorLayer`, cursor fields, throttling, coordinate mapping) | The whole point of the simplification |
| Shared view state (`sharedView.ts`, `useSharedView/Section/Input`, `TypistChip`, the `presence/{code}/shared/view` document and its rules) | Panels and inputs go back to plain local state |
| Shared/solo toggle (`SharedViewToggle`, `ViewMode`, `mode` plumbing) | With nothing to switch off, a toggle for "am I visible" is ceremony — presence is just a fact about the page |
| Solo-vs-shared search (`searchMode.ts`, the `ownBotSearch` bookkeeping) | Restores `SearchPanel` to its original behaviour: search goes through the bot and everyone sees the results, as it did before today |
| The `PresenceLayer` re-render isolation (children-as-props, the portal, rect measurement) | It existed **because** cursors arrived tens of times per second. Presence now changes on join, leave, focus, and rename — a handful of events per session — so the machinery costs more than it saves |

Deleting the isolation is worth stating plainly: it was correct for the problem it had, and that problem is gone with the feature that caused it.

## What is kept and changed

| Decision | |
|---|---|
| Who can **see** presence | **Everyone, including signed-out visitors.** The session code is already the capability for everything else on this dashboard; who is looking is no more sensitive than the queue. Rules relax from `read: if request.auth != null` to `read: if true`. |
| Who **appears** | Signed-in users only. Appearing requires a uid, which is what the uid-scoped write rule is built on; an anonymous browser has no stable identity to attribute an avatar to. |
| Focus state | A new `focused` boolean on the participant document. True when the tab is visible **and** the window has focus. Unfocused participants render greyed out — "here but not looking". |
| Hover | A tooltip revealing the display name. Replaces the bare `title` attribute so it is styled and readable. |
| Self | **Included** in the bar (it was previously filtered out), marked as you, and clicking it opens the nickname editor. |
| Nicknames | `identity.ts` already stores one. Today `buildSnapshot` computes `accountName \|\| nickname`, so **a signed-in user's nickname is ignored**. That inverts to `nickname \|\| accountName`, and presence publishes the effective identity name rather than `user.displayName`. |

## Components

### `src/lib/presence.ts` (simplified)

Drops `ViewMode`, `Point`, `toNormalized`, `toPixels`, `movedEnough`, and the `CURSOR_*` constants. `Participant` becomes `{ uid, name, photoURL, color, focused, updatedAt }`. `shouldPublish(signedIn)` loses its mode argument — it remains the single auth gate. `livingParticipants(all, now)` no longer excludes self, since self is now shown; the self-exclusion moves to the caller's rendering decision rather than being baked into liveness.

`colorForUid` and `isAllowedPhotoUrl` are unchanged and keep their tests.

### `src/hooks/usePresence.ts` (simplified)

No cursor path. Publishes `{ name, photoURL, color, focused, updatedAt: serverTimestamp() }`, heartbeats every 15 s, deletes on unload. Subscribes **whenever there is a session code**, signed in or not — reading is public now. Tracks focus from `visibilitychange`, `focus`, and `blur`, writing only on an actual change of state rather than on every event.

The name comes from `identity.ts`'s `useIdentity()`, so setting a nickname republishes without a reload.

### `src/components/PresenceBar.tsx` (reworked)

Avatar row including yourself. Each avatar: photo or initial, a ring in the participant's colour, `opacity` reduced and saturation dropped when `focused` is false. A hover tooltip gives the display name and, for unfocused people, a hint that they are away. Yours is clickable and opens the nickname editor.

### `src/lib/identity.ts` (one-line priority change)

`nickname || accountName || ANONYMOUS_NAME`. `IdentityChip` already provides the click-to-edit UI and needs no change beyond wording that no longer implies nicknames are for anonymous visitors only.

### `firestore.rules`

- `presence/{sessionCode}/participants/{uid}`: `read: if true`; write still uid-scoped and server-stamped; `cursor` replaced by `focused is bool`.
- `presence/{sessionCode}/shared/view`: **deleted**.

## Security and privacy

- **Presence becomes publicly readable to anyone holding the session code.** That is a deliberate widening, justified by the code already granting full playback control; it is stated here rather than buried.
- **Writes stay uid-scoped and server-stamped**, so nobody can create, move, or age someone else's avatar.
- **`photoURL` stays restricted** to `*.googleusercontent.com` in rules and on render — it is an `<img src>` shown to every viewer, so an arbitrary host would be an IP/User-Agent beacon.
- **Nicknames are display-only**, capped at 32 characters, and carry no authority; the `name` field is already length-capped in rules.
- Removing the shared-view document removes an entire write surface, including the impersonation and force-collapse holes that had to be closed for it.

## Error handling

| Condition | Result |
|---|---|
| Signed out | Sees the bar; does not appear in it |
| Presence listener fails | Bar renders empty; the dashboard is unaffected |
| Tab hidden or window blurred | That participant greys out for everyone |
| Participant crashes | Filtered after the 45 s staleness window, as before |
| Nickname set | Republishes immediately; others see the new name without reloading |

## Testing

- **`presence.ts`:** colour stability; staleness boundary; self **retained**; `shouldPublish` false when signed out.
- **Focus:** a pure `isFocused(visibility, hasFocus)` helper, so the three-event tracking has one testable decision.
- **`identity.ts`:** nickname wins over the account name; clearing it falls back to the account name; anonymous still gets "Web User".
- **`PresenceBar`:** greys an unfocused participant; shows the name on hover; marks and makes yours clickable; renders for signed-out viewers.
- **Regression:** the six panels and `SearchPanel` behave as they did before the shared-view feature.
- **Manual:** two browsers — the second appears; switch tabs and it greys on the first; set a nickname and the name updates live; a signed-out window sees both and appears in neither.

## Out of scope

Anonymous participants appearing in the bar, per-user avatars for Discord identities, presence anywhere but the dashboard, and any return of cursors or view sync.
