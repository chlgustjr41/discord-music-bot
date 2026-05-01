# Playlist Edit Dialog — Redesign

**Date:** 2026-05-01
**Status:** Approved (pending plan)
**Owner:** Jacob (Hyunsirk) Choi
**Affected surface:** `frontend/src/components/PlaylistManager.tsx`, Firestore security rules

## Problem

The current playlist create/edit dialog only lets the user pick from a fixed unified pool (queue + music history) via checkboxes. There is no way to:

1. Search for a specific song within the available pool.
2. Search for a song online (YouTube / SoundCloud / Bandcamp) without leaving the dialog.
3. Reorder tracks once they are in the playlist.
4. Pull tracks from another saved playlist on the same server.
5. Rename a playlist while editing.

The dialog also silently overwrites a playlist when a name collision occurs, because the playlist's name is its Firestore doc ID and `setDoc` is upsert-by-default.

## Goal

Redesign the create/edit dialog so the user can build and curate a playlist from three sources — existing tracks, the internal pool (queue / history / other playlists), and the online search — and reorder freely. Make the save path safe against name collisions.

## Out of Scope

- Persisting per-region resize heights to `localStorage` (follow-up).
- Adding a frontend test framework (the project has none today).
- Refactoring the global `SearchPanel` to share `useBotSearch` (the new hook is reusable; the cleanup is a separate ticket).
- Bot-side changes to support multiple concurrent search slots (single-slot remains; we mitigate with per-requester gating).

## Layout

The dialog uses three vertically stacked, independently resizable regions inside the existing `Dialog` container (current width preserved). All resize handles use the existing `ResizableList` component.

```
┌─ Edit Playlist · "Chill Vibes" ──────────────────┐
│ [ Name input (editable) ]                        │
│                                                  │
│ ┌─ Playlist (12 tracks) ────────────────────┐    │
│ │ ≡ 1. Track A           3:42  [×]          │    │ ← drag to reorder
│ │ ≡ 2. Track B           2:58  [×]          │    │
│ │ …                                         │    │
│ │ ──── resize handle ────                   │    │
│ └───────────────────────────────────────────┘    │
│                                                  │
│ ┌─ [ Internal | Online ] ───────────────────┐    │
│ │ Filter / Search input                     │    │
│ │ ─ Result A          [queue]   [+]         │    │
│ │ ─ Result B          [history] [+]         │    │
│ │ ─ Result C          [Workout] [+]         │    │
│ │ ─ Already added     [in draft][✓ disabled]│    │
│ │ ──── resize handle ────                   │    │
│ └───────────────────────────────────────────┘    │
│                                                  │
│ [Cancel]                       [Save (12)]       │
└──────────────────────────────────────────────────┘
```

**Tab semantics:**
- **Internal** — client-side filter over the unified pool: queue + music history + tracks from all *other* saved playlists on this server. Each result shows a source badge (`queue` / `history` / `<playlist name>`). The filter is a case-insensitive substring match against `title + " " + artist`.
- **Online** — bot-mediated search via the existing `searchQuery` / `searchResults` Firestore round-trip. Accepts both queries and URLs (YouTube video, YouTube playlist, SoundCloud, Bandcamp). When the bot returns `searchPlaylistName`, the tab shows an "Imported from: <name>" banner with an **Add all** button.

The legacy "Import URL" mode is removed; URLs are accepted in the Online tab.

## Component & file layout

Today's `PlaylistManager.tsx` (~700 lines) bundles the list view, the create/edit dialog, and the import flow. We split into a folder:

```
frontend/src/components/playlist/
  PlaylistManager.tsx          // existing list + open-dialog buttons (slimmed down)
  PlaylistEditDialog.tsx       // dialog (replaces current inline JSX in PlaylistManager)
  PlaylistDraftList.tsx        // sortable draft (uses @dnd-kit/sortable)
  PlaylistInternalSearch.tsx   // tab content: filter unified pool client-side
  PlaylistOnlineSearch.tsx     // tab content: bot-mediated search
  hooks/
    useUnifiedTrackPool.ts     // queue + history + other-playlists fetch + dedupe
    useBotSearch.ts            // wraps Firestore searchQuery/searchResults round-trip
```

Each file targets <200 lines and has one job. `useBotSearch` is reusable by `SearchPanel` later but that refactor is out of scope here.

## State & data flow

Three independent state trees inside `PlaylistEditDialog`:

```ts
const [draft, setDraft] = useState<Track[]>([]);            // ordered; source of truth for save
const [filter, setFilter] = useState("");                    // internal search text
const botSearch = useBotSearch(serverId);                    // {query, results, loading, error, playlistName, search()}
const pool = useUnifiedTrackPool(serverId, currentQueue, currentTrack, editingPlaylist);
```

**`useUnifiedTrackPool`** (replaces `loadUnifiedTracks` from current code):
1. Read `currentTrack` and `currentQueue` from props (already in scope).
2. `getDocs` on `servers/{serverId}/musicHistory` (limit 100, ordered by `addedAt desc`) — same as today.
3. **New:** `getDocs` on `servers/{serverId}/playlists` and flatten `tracks` from every playlist *except the one being edited*.
4. Dedupe by URL across all sources, in order: existing playlist tracks → currentTrack → queue → history → other playlists. First source wins (matches today).
5. Returns `{ pool: UnifiedTrack[], loading: boolean }`.

**`useBotSearch`**:
- Owns a `requesterId` (random per hook instance) used to gate `useEffect` that consumes `searchResults`. Each search is a write of `{searchQuery, searchResults: [], _requester: requesterId}` to the server doc; the hook only consumes results when its `_requester` matches. This is what prevents the dialog and the global `SearchPanel` from clobbering each other.
- Provides: `query`, `results`, `loading`, `error`, `playlistName`, `search(q)`, `clear()`.
- 15s timeout (matches `SearchPanel`).
- *(Note: this requires the bot to echo `_requester` in its response. If that's a non-trivial bot change, fall back to per-component `waitingForResults` ref — the current pattern — and accept "last write wins" as a known minor footgun.)*

**Adding from search to draft:**
- Click `+` on a search result → prepend the `Track` to `draft` (top-of-playlist add).
- If the URL already exists in `draft`, the `+` is replaced by a `✓ in draft` chip; the row is non-interactive.
- `requestedBy` field on the added Track: preserved from the source for internal-search adds (the queue/history/other-playlist `requestedBy` carries through); set to `"Web User"` for online-search adds (matches today's behavior).
- **Add all** (visible only when an Online-tab playlist URL was resolved): prepends every result that is not already in the draft, in the order returned by the bot. Results that are already in the draft are skipped silently (no error, no count adjustment).

**Removing from draft:** `×` button on each draft row → `draft.filter(t => t.url !== url)`.

**Reorder:** `@dnd-kit/sortable` with `verticalListSortingStrategy`. Grip icon on each row is the drag handle. On drop, `arrayMove(draft, oldIndex, newIndex)`. Touch + keyboard sensors enabled.

## Save semantics (create + rename + dup-check)

```ts
async function save() {
  const newName = playlistName.trim();
  if (!newName || draft.length === 0) return;

  const isRename = editingPlaylist && newName !== editingPlaylist.name;
  const isCreate = !editingPlaylist;

  // Duplicate-name check (skip if rename target == current name)
  if (isCreate || isRename) {
    const existing = await getDoc(doc(db, "servers", serverId, "playlists", newName));
    if (existing.exists()) {
      setSaveError(`A playlist named "${newName}" already exists.`);
      return;
    }
  }

  const payload = {
    name: newName,
    tracks: draft,
    createdBy: editingPlaylist?.createdBy ?? "Web User",
    createdAt: editingPlaylist?.createdAt ?? serverTimestamp(),
  };

  const batch = writeBatch(db);
  batch.set(doc(db, "servers", serverId, "playlists", newName), payload);
  if (isRename) {
    batch.delete(doc(db, "servers", serverId, "playlists", editingPlaylist.name));
  }
  await batch.commit();
}
```

## Firestore security rule

Add (or amend) the playlist rule so a `setDoc` on an existing doc cannot overwrite it — `create` only:

```
match /servers/{sid}/playlists/{name} {
  allow read: if request.auth != null;
  allow create: if request.auth != null;
  allow update, delete: if request.auth != null;
}
```

The client-side dup-check above is the friendly path; the rule is the defensive backstop against the two-tabs-saving-the-same-name race. A renamed playlist's batch is `create new + delete old`, both allowed.

## Edge cases

| Case | Behavior |
| --- | --- |
| Empty draft on save | Save button disabled. |
| Empty name on save | Save button disabled. |
| Loading internal pool | "Loading…" skeleton inside Internal tab; dialog not blocked. |
| Online search timeout (15s) | "Search timed out. Make sure the bot is connected." |
| Online search no results | "No results found." with the query echoed. |
| Pasted YouTube playlist URL | Bot returns `searchPlaylistName` → banner "Imported from: <name> · N tracks" + **Add all** button. |
| Track already in draft, +'d again in search | `+` replaced by `✓ in draft` chip; no-op. |
| Rename to current name | Treated as no-op rename — skip dup check, just update tracks. |
| Rename to existing name | Inline error under name input; nothing written. |
| Two tabs save the same new name | Firestore `allow create` blocks the loser; loser sees "Save failed — name may be taken." |
| Track URL collision across pool sources | First source wins (existing dedupe order: playlist → queue → history → other playlists). |
| Cancel mid-edit | Discard draft state. Same as today. |
| Dialog closed via overlay click | Same as Cancel — discard. |

## Concurrency with global SearchPanel

The global `SearchPanel` and the dialog's Online tab both write to `servers/{id}.searchQuery`. Today, each component uses a `waitingForResults` ref to ignore results it didn't request, so a search-while-dialog-open results in **one of the two requests winning** and the other component eventually timing out (15s). This is a pre-existing limitation, not a regression.

The `useBotSearch` hook can optionally tag requests with a `_requester` ID and only consume responses that match — but only if the bot is updated to echo the field. If that's a non-trivial bot change, we keep today's `waitingForResults` pattern. **Default plan: keep `waitingForResults`; flag `_requester` as a future improvement.**

## Manual test checklist

The frontend has no test framework today. We add no framework as part of this change. Manual smoke checklist for the implementer:

1. **Create with internal search** — open new dialog, filter internal pool, +Add three tracks, drag to reorder, save → playlist exists in Firestore with expected order.
2. **Create with online search** — same but Online tab; verify timeout banner appears if bot is offline.
3. **Edit existing — reorder** — open existing playlist, drag rows, save → tracks array order in Firestore matches the new order.
4. **Edit existing — add via internal** — open existing, switch to Internal tab, add a track from another playlist, save → new track is at top of `tracks`.
5. **Duplicate-name on create** — create a playlist named "Test"; create another also named "Test" → see inline error, no Firestore write.
6. **Rename to free name** — edit "Test", change name to "Test2", save → "Test" deleted, "Test2" present with same tracks.
7. **Rename to taken name** — edit "Test", change name to an existing playlist's name → see inline error.
8. **Already-in-draft signal** — internal search shows `✓ in draft` chip on a row whose URL is already in the draft; the `+` is non-interactive.
9. **Playlist URL paste in Online** — paste a YouTube playlist URL → banner appears with playlist name; **Add all** prepends every track.
10. **Concurrency** — open the global `SearchPanel`, start a search; before it returns, open the edit dialog and start an online search → either component eventually shows results or a timeout error; neither corrupts the other's selection state.
11. **Resize regions** — drag each region's resize handle → list height changes within min/max bounds; close + reopen dialog → defaults restored (no persistence by design).
12. **Mobile drag-to-reorder** — on a touch device, long-press a row's grip and drag → reorders.

## Implementation notes (non-binding)

- The new folder `frontend/src/components/playlist/` does not yet exist; everything is in `components/`. Importers from outside (currently only `Dashboard.tsx`) update to the new path.
- `lucide-react` icons already used: `GripVertical` for the row drag handle, `X` for remove, `Plus` for add, `Check` for the "in draft" chip.
- `@dnd-kit/core`, `@dnd-kit/sortable`, `@dnd-kit/utilities` are already in `package.json`.
- Existing `ResizableList` (`frontend/src/components/ResizableList.tsx`) is reused as-is.
- The current `PlaylistManager.tsx` will lose the Create/Edit dialog JSX (~250 lines) and the import-mode state (~50 lines); it shrinks to mostly the list view + the open-dialog buttons.
