# Presence for Everyone — Design

**Date:** 2026-08-10
**Status:** Approved
**Scope:** Fixes the periodic avatar flicker, and makes signed-out visitors appear in the presence bar alongside signed-in ones. Touches `frontend/` and `firestore.rules`.

## Problem

Two reports from testing, with different causes.

### 1. The avatar flickers every few seconds — a real bug

Every heartbeat writes `updatedAt: serverTimestamp()`. Firestore fires a **local** snapshot immediately, before the server responds, with that field still `null`. `toMillis(null)` returns `NaN`, and `livingParticipants` rejects any row whose `updatedAt` is not finite — so your own avatar disappears for the duration of the round trip and comes back when the write is acknowledged. At `HEARTBEAT_MS = 15_000` that is a visible blink roughly every 15 seconds, and it also happens on every nickname change and every focus change.

The guard itself is right: an unresolved timestamp genuinely is not a time, and treating `NaN` as fresh would let a malformed row live forever. What is wrong is throwing away the one piece of information that settles it — Firestore marks the document `metadata.hasPendingWrites`, which means *this browser just wrote it*. A row you are in the middle of writing is the one row whose liveness is not in question.

### 2. Only one person shows — not a defect, a design limit

Verified against the deployed ruleset in the emulator: two signed-in users both write successfully, and every viewer — including an unauthenticated one — lists both documents. The read path, `PresenceBar`, and the dashboard wiring are all correct.

What the current design does is refuse to *publish* for anyone who is not signed in. A signed-out participant therefore sees everyone else but never appears, and if only one person in a test was signed in, exactly one avatar exists. That is the behaviour being changed here.

## Decisions

| Question | Decision |
|---|---|
| Pending writes | A document with `metadata.hasPendingWrites` and an unresolved `updatedAt` is treated as **fresh now**. It is your own in-flight write; nothing else can produce that combination. |
| Who appears | **Everyone on the dashboard**, signed in or not. Presence stops being a property of having an account and becomes a property of having the page open. |
| Anonymous identity | The stable per-browser id already used for leaderboard keying (`getMemberKey`), namespaced as `anon_<id>` so the rules can tell the two kinds apart by document id alone. |
| Anonymous names | Their nickname if they set one; otherwise **"Anonymous N"**. |
| Numbering | Assigned client-side by sorting un-named anonymous rows by document id — deterministic, so **every viewer sees the same person as "Anonymous 1"**. It is not join-order: `updatedAt` moves on every heartbeat, and a write-once `joinedAt` would be clobbered by the merge writes. Numbering shifts when someone leaves; that is accepted rather than papered over. |
| Anonymous avatars | No photo — a letter or dot avatar in the same uid-derived colour signed-in users get, so "random colour" is stable per browser rather than re-rolled on render. |
| Real-time | Already a collection `onSnapshot`; joins and leaves propagate with no reload. The flicker fix removes the only thing that made it *look* unreliable. |

## Security

Anonymous rows cannot be uid-scoped — there is no uid. The rules therefore split on the document id:

- `presence/{code}/participants/{uid}` where the id equals `request.auth.uid` → signed-in, as today.
- the same path where the id matches `anon_[A-Za-z0-9_-]{8,64}` → writable **without auth**.

The consequence, stated plainly: **anyone can create, overwrite, or delete any anonymous presence row.** A griefer with the session code could remove someone's avatar or add fake ones. That is accepted because it is the same trust level the rest of this app already runs at — `servers/{id}` and every subcollection under it are `allow read, write: if true`, so the queue, playback and history are already fully writable by anyone holding the code. A presence row is strictly less valuable than the queue. Signed-in rows keep their uid scoping, so an account's avatar still cannot be forged or removed by anyone else.

Everything else is unchanged: `updatedAt` stays server-stamped, the field shape stays constrained, `name` stays length-capped, and `photoURL` stays restricted to `*.googleusercontent.com` — anonymous rows may not set one at all.

## Components

- **`src/lib/presence.ts`** — `presenceIdFor(uid, browserId)` returning `uid` or `anon_<browserId>`; `isAnonymousId(id)`; `withDisplayNames(participants)` assigning "Anonymous N" deterministically. All pure, all tested.
- **`src/hooks/usePresence.ts`** — publishes for everyone; `toParticipant` takes `hasPendingWrites` and resolves a pending `updatedAt` to now; anonymous writes omit `photoURL` and set `anon: true`.
- **`src/components/PresenceBar.tsx`** — renders the resolved display name; self-detection keys on the presence id rather than the uid, so an anonymous visitor can still click their own badge to set a nickname.
- **`firestore.rules`** — the split above.

## Error handling

| Condition | Result |
|---|---|
| Own write in flight | Row stays visible (the fix) |
| Unresolved `updatedAt` with no pending write | Still filtered — a malformed row must not become immortal |
| Anonymous visitor with no nickname | "Anonymous N", numbered identically for every viewer |
| Anonymous visitor sets a nickname | Republishes; name updates live for everyone |
| Anonymous row deleted by someone else | It reappears on the next heartbeat, within 15 s |

## Testing

- `toParticipant`: pending + unresolved → fresh; not pending + unresolved → filtered.
- `livingParticipants`: unchanged; the NaN guard stays and keeps its test.
- `presenceIdFor` / `isAnonymousId`: round-trip, and a uid is never mistaken for an anonymous id.
- `withDisplayNames`: numbering is stable across two viewers given the same input in different array orders; a nickname suppresses the number; signed-in rows are untouched.
- Rules, in the emulator: an anonymous client may write `anon_*` and may not write a uid-shaped id; a signed-in client may not write another uid; everyone can list.
- Manual: two browsers, one signed out — both appear; the signed-out one shows "Anonymous 1"; setting a nickname updates it live; neither flickers.

## Out of scope

Server-assigned stable numbering, anonymous avatars beyond a letter, and any return of cursors or view sync.
