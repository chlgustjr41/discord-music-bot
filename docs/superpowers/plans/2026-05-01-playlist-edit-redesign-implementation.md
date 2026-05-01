# Playlist Edit Dialog — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the playlist create/edit dialog with a sortable draft, internal/online search tabs, rename support with duplicate-name guard, and resizable regions.

**Architecture:** The current monolithic `PlaylistManager.tsx` (~700 lines) is split into a `playlist/` folder with one orchestrating dialog (`PlaylistEditDialog`), three child views (`PlaylistDraftList`, `PlaylistInternalSearch`, `PlaylistOnlineSearch`), and two hooks (`useUnifiedTrackPool`, `useBotSearch`). State is local to the dialog; persistence is on Save via a writeBatch (create + delete-old for rename).

**Tech Stack:** React 19, TypeScript, Vite, Firebase Firestore (web SDK), `@dnd-kit/core` + `@dnd-kit/sortable` (already installed), existing `ResizableList` component, `lucide-react` icons.

**Reference spec:** `docs/superpowers/specs/2026-05-01-playlist-edit-redesign-design.md`

**Testing note:** The frontend has no test framework. Each task ends with `npm run build` (TypeScript + Vite) as the verification gate. A final manual QA task runs the spec's smoke checklist.

---

## File map

**Created:**
- `frontend/src/components/playlist/PlaylistEditDialog.tsx`
- `frontend/src/components/playlist/PlaylistDraftList.tsx`
- `frontend/src/components/playlist/PlaylistInternalSearch.tsx`
- `frontend/src/components/playlist/PlaylistOnlineSearch.tsx`
- `frontend/src/components/playlist/types.ts`
- `frontend/src/components/playlist/hooks/useUnifiedTrackPool.ts`
- `frontend/src/components/playlist/hooks/useBotSearch.ts`

**Modified:**
- `frontend/src/components/PlaylistManager.tsx` (removes ~300 lines of dialog + import logic; imports new dialog)
- `frontend/src/components/Dashboard.tsx` (no path change — `PlaylistManager` keeps its location)
- `firestore.rules` (split playlist write into create/update/delete)

---

## Task 1: Create folder skeleton + shared types

**Files:**
- Create: `frontend/src/components/playlist/types.ts`

- [ ] **Step 1: Create the types file**

Write `frontend/src/components/playlist/types.ts`:

```ts
import type { Track } from "../../types";

export interface PlaylistDoc {
  name: string;
  tracks: Track[];
  createdBy: string;
  createdAt: unknown;
}

export type UnifiedTrackSource =
  | "queue"
  | "history"
  | "playlist"          // existing tracks of the playlist being edited
  | { kind: "other-playlist"; name: string };

export interface UnifiedTrack extends Track {
  source: UnifiedTrackSource;
}
```

- [ ] **Step 2: Build verify**

Run: `cd frontend && npm run build`
Expected: build succeeds with no errors (file is unused so far but must type-check).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/playlist/types.ts
git commit -m "refactor(playlist): add shared types for playlist folder"
```

---

## Task 2: Implement `useUnifiedTrackPool` hook

**Files:**
- Create: `frontend/src/components/playlist/hooks/useUnifiedTrackPool.ts`

- [ ] **Step 1: Write the hook**

Write `frontend/src/components/playlist/hooks/useUnifiedTrackPool.ts`:

```ts
import { useState, useCallback, useEffect } from "react";
import {
  collection,
  getDocs,
  query,
  orderBy,
  limit,
} from "firebase/firestore";
import { db } from "../../../firebase";
import type { Track, CurrentTrack, MusicHistoryEntry } from "../../../types";
import type { UnifiedTrack, PlaylistDoc } from "../types";

interface Args {
  serverId: string;
  currentQueue: Track[];
  currentTrack: CurrentTrack | null;
  /** When editing: the playlist whose tracks should appear first and which should be excluded from "other playlists" pool. */
  editingPlaylistName?: string;
  existingTracks?: Track[];
  /** Re-fetch trigger; bump to force refresh. */
  refreshKey?: number;
}

export function useUnifiedTrackPool({
  serverId,
  currentQueue,
  currentTrack,
  editingPlaylistName,
  existingTracks,
  refreshKey = 0,
}: Args) {
  const [pool, setPool] = useState<UnifiedTrack[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    const seen = new Set<string>();
    const out: UnifiedTrack[] = [];

    if (existingTracks) {
      for (const t of existingTracks) {
        if (t.url && !seen.has(t.url)) {
          seen.add(t.url);
          out.push({ ...t, source: "playlist" });
        }
      }
    }

    if (currentTrack && !seen.has(currentTrack.url)) {
      seen.add(currentTrack.url);
      out.push({
        title: currentTrack.title,
        artist: currentTrack.artist,
        url: currentTrack.url,
        thumbnail: currentTrack.thumbnail,
        duration: currentTrack.duration,
        requestedBy: currentTrack.requestedBy,
        source: "queue",
      });
    }
    for (const t of currentQueue) {
      if (!seen.has(t.url)) {
        seen.add(t.url);
        out.push({ ...t, source: "queue" });
      }
    }

    try {
      const histQ = query(
        collection(db, "servers", serverId, "musicHistory"),
        orderBy("addedAt", "desc"),
        limit(100)
      );
      const histSnap = await getDocs(histQ);
      for (const d of histSnap.docs) {
        const h = d.data() as MusicHistoryEntry;
        if (!seen.has(h.url)) {
          seen.add(h.url);
          out.push({
            title: h.title,
            artist: h.artist,
            url: h.url,
            thumbnail: h.thumbnail,
            duration: h.duration || 0,
            requestedBy: h.requestedBy || "Web User",
            source: "history",
          });
        }
      }
    } catch {
      /* fall through */
    }

    try {
      const plSnap = await getDocs(
        collection(db, "servers", serverId, "playlists")
      );
      for (const d of plSnap.docs) {
        if (d.id === editingPlaylistName) continue;
        const data = d.data() as PlaylistDoc;
        for (const t of data.tracks ?? []) {
          if (t.url && !seen.has(t.url)) {
            seen.add(t.url);
            out.push({
              ...t,
              source: { kind: "other-playlist", name: d.id },
            });
          }
        }
      }
    } catch {
      /* fall through */
    }

    setPool(out);
    setLoading(false);
  }, [serverId, currentQueue, currentTrack, editingPlaylistName, existingTracks]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  return { pool, loading, reload: load };
}
```

- [ ] **Step 2: Build verify**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/playlist/hooks/useUnifiedTrackPool.ts
git commit -m "feat(playlist): add useUnifiedTrackPool hook"
```

---

## Task 3: Implement `useBotSearch` hook

**Files:**
- Create: `frontend/src/components/playlist/hooks/useBotSearch.ts`

- [ ] **Step 1: Write the hook**

Write `frontend/src/components/playlist/hooks/useBotSearch.ts`:

```ts
import { useState, useEffect, useRef, useCallback } from "react";
import { doc, updateDoc } from "firebase/firestore";
import { db } from "../../../firebase";
import type { SearchResult } from "../../../types";

interface Args {
  serverId: string;
  /** Live searchResults from server doc subscription (passed from parent). */
  searchResults?: SearchResult[];
  /** Live searchQuery from server doc subscription. Cleared by bot when done. */
  searchQuery?: string | null;
  /** Live searchPlaylistName from server doc subscription. Set by bot when results came from a playlist URL. */
  searchPlaylistName?: string | null;
  /** Timeout in ms for the bot to respond (default 15000). */
  timeoutMs?: number;
}

interface State {
  query: string;
  results: SearchResult[];
  playlistName: string | null;
  loading: boolean;
  error: string;
}

export function useBotSearch({
  serverId,
  searchResults,
  searchQuery,
  searchPlaylistName,
  timeoutMs = 15000,
}: Args) {
  const [state, setState] = useState<State>({
    query: "",
    results: [],
    playlistName: null,
    loading: false,
    error: "",
  });
  const waitingForResults = useRef(false);
  const lastSentQuery = useRef("");

  // Consume bot-returned results when we are the requester
  useEffect(() => {
    if (!waitingForResults.current) return;
    if (searchQuery) return; // bot still processing
    if (searchResults && searchResults.length > 0) {
      setState((s) => ({
        ...s,
        results: searchResults,
        playlistName: searchPlaylistName ?? null,
        loading: false,
        error: "",
      }));
    } else {
      setState((s) => ({
        ...s,
        results: [],
        playlistName: null,
        loading: false,
        error: "No results found.",
      }));
    }
    waitingForResults.current = false;
  }, [searchResults, searchQuery, searchPlaylistName]);

  // Timeout
  useEffect(() => {
    if (!state.loading) return;
    const t = setTimeout(() => {
      if (waitingForResults.current) {
        waitingForResults.current = false;
        setState((s) => ({
          ...s,
          loading: false,
          error: "Search timed out. Make sure the bot is connected.",
        }));
      }
    }, timeoutMs);
    return () => clearTimeout(t);
  }, [state.loading, timeoutMs]);

  const search = useCallback(
    async (q: string) => {
      const trimmed = q.trim();
      if (!trimmed) return;
      if (trimmed === lastSentQuery.current && state.results.length > 0) return;
      lastSentQuery.current = trimmed;
      setState({
        query: trimmed,
        results: [],
        playlistName: null,
        loading: true,
        error: "",
      });
      waitingForResults.current = true;
      try {
        await updateDoc(doc(db, "servers", serverId), {
          searchQuery: trimmed,
          searchResults: [],
        });
      } catch {
        waitingForResults.current = false;
        setState({
          query: trimmed,
          results: [],
          playlistName: null,
          loading: false,
          error: "Search failed.",
        });
      }
    },
    [serverId, state.results.length]
  );

  const clear = useCallback(() => {
    waitingForResults.current = false;
    lastSentQuery.current = "";
    setState({
      query: "",
      results: [],
      playlistName: null,
      loading: false,
      error: "",
    });
  }, []);

  return { ...state, search, clear };
}
```

- [ ] **Step 2: Build verify**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/playlist/hooks/useBotSearch.ts
git commit -m "feat(playlist): add useBotSearch hook"
```

---

## Task 4: Implement `PlaylistDraftList` component

**Files:**
- Create: `frontend/src/components/playlist/PlaylistDraftList.tsx`

- [ ] **Step 1: Write the component**

Write `frontend/src/components/playlist/PlaylistDraftList.tsx`:

```tsx
import {
  DndContext,
  closestCenter,
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  verticalListSortingStrategy,
  arrayMove,
  useSortable,
  sortableKeyboardCoordinates,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical, Music, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ResizableList } from "../ResizableList";
import type { Track } from "../../types";

interface Props {
  draft: Track[];
  onChange: (next: Track[]) => void;
  formatDuration: (s: number) => string;
}

function SortableRow({
  track,
  index,
  onRemove,
  formatDuration,
}: {
  track: Track;
  index: number;
  onRemove: () => void;
  formatDuration: (s: number) => string;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: track.url });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
  };
  return (
    <li
      ref={setNodeRef}
      style={style}
      className="flex items-center gap-2 rounded p-1.5 hover:bg-muted/50"
    >
      <button
        {...attributes}
        {...listeners}
        type="button"
        className="cursor-grab touch-none text-muted-foreground/60 hover:text-foreground"
        aria-label="Drag to reorder"
      >
        <GripVertical className="h-4 w-4" />
      </button>
      <span className="w-5 text-right text-xs text-muted-foreground/60">{index + 1}.</span>
      {track.thumbnail ? (
        <img src={track.thumbnail} alt="" className="h-7 w-7 rounded object-cover shrink-0" />
      ) : (
        <div className="flex h-7 w-7 items-center justify-center rounded bg-muted shrink-0">
          <Music className="h-3 w-3 text-muted-foreground" />
        </div>
      )}
      <div className="flex-1 min-w-0">
        <p className="truncate text-sm">{track.title}</p>
        <p className="truncate text-xs text-muted-foreground">
          {track.artist}
          {track.duration > 0 && ` — ${formatDuration(track.duration)}`}
        </p>
      </div>
      <Button
        size="sm"
        variant="ghost"
        className="text-destructive hover:text-destructive"
        onClick={onRemove}
        aria-label="Remove from draft"
      >
        <X className="h-3.5 w-3.5" />
      </Button>
    </li>
  );
}

export function PlaylistDraftList({ draft, onChange, formatDuration }: Props) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = draft.findIndex((t) => t.url === active.id);
    const newIndex = draft.findIndex((t) => t.url === over.id);
    if (oldIndex < 0 || newIndex < 0) return;
    onChange(arrayMove(draft, oldIndex, newIndex));
  }

  if (draft.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center rounded-md border py-6 text-muted-foreground">
        <Music className="mb-1 h-6 w-6" />
        <p className="text-sm">Add tracks below to start the playlist.</p>
      </div>
    );
  }

  return (
    <ResizableList defaultHeight={200} minHeight={80} maxHeight={400} className="rounded-md border">
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={draft.map((t) => t.url)} strategy={verticalListSortingStrategy}>
          <ul className="p-1">
            {draft.map((t, i) => (
              <SortableRow
                key={t.url}
                track={t}
                index={i}
                onRemove={() => onChange(draft.filter((x) => x.url !== t.url))}
                formatDuration={formatDuration}
              />
            ))}
          </ul>
        </SortableContext>
      </DndContext>
    </ResizableList>
  );
}
```

- [ ] **Step 2: Build verify**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/playlist/PlaylistDraftList.tsx
git commit -m "feat(playlist): add PlaylistDraftList sortable component"
```

---

## Task 5: Implement `PlaylistInternalSearch` component

**Files:**
- Create: `frontend/src/components/playlist/PlaylistInternalSearch.tsx`

- [ ] **Step 1: Write the component**

Write `frontend/src/components/playlist/PlaylistInternalSearch.tsx`:

```tsx
import { useMemo } from "react";
import { Check, Music, Plus, Loader2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ResizableList } from "../ResizableList";
import type { UnifiedTrack } from "./types";

interface Props {
  pool: UnifiedTrack[];
  poolLoading: boolean;
  filter: string;
  onFilterChange: (s: string) => void;
  draftUrls: Set<string>;
  onAdd: (track: UnifiedTrack) => void;
  formatDuration: (s: number) => string;
}

function sourceLabel(source: UnifiedTrack["source"]): string {
  if (source === "queue") return "queue";
  if (source === "history") return "history";
  if (source === "playlist") return "playlist";
  return source.name;
}

export function PlaylistInternalSearch({
  pool,
  poolLoading,
  filter,
  onFilterChange,
  draftUrls,
  onAdd,
  formatDuration,
}: Props) {
  const filtered = useMemo(() => {
    const f = filter.trim().toLowerCase();
    if (!f) return pool;
    return pool.filter((t) =>
      `${t.title} ${t.artist}`.toLowerCase().includes(f)
    );
  }, [pool, filter]);

  return (
    <div className="space-y-2">
      <Input
        type="text"
        value={filter}
        onChange={(e) => onFilterChange(e.target.value)}
        placeholder="Filter queue, history, and other playlists…"
      />
      <ResizableList
        defaultHeight={200}
        minHeight={80}
        maxHeight={400}
        className="rounded-md border"
      >
        {poolLoading ? (
          <div className="flex items-center justify-center py-6 text-muted-foreground gap-2">
            <Loader2 className="h-4 w-4 animate-spin" />
            <p className="text-sm">Loading tracks…</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-6 text-muted-foreground">
            <Music className="mb-1 h-6 w-6" />
            <p className="text-sm">
              {filter.trim() ? "No matches." : "Nothing to show. Play some music first!"}
            </p>
          </div>
        ) : (
          <ul className="p-1 space-y-0.5">
            {filtered.map((t) => {
              const inDraft = draftUrls.has(t.url);
              return (
                <li
                  key={t.url}
                  className="flex items-center gap-2 rounded-md p-2 hover:bg-muted/50"
                >
                  {t.thumbnail ? (
                    <img src={t.thumbnail} alt="" className="h-7 w-7 rounded object-cover shrink-0" />
                  ) : (
                    <div className="flex h-7 w-7 items-center justify-center rounded bg-muted shrink-0">
                      <Music className="h-3 w-3 text-muted-foreground" />
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <p className="truncate text-sm font-medium">{t.title}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      {t.artist}
                      {t.duration > 0 && ` — ${formatDuration(t.duration)}`}
                    </p>
                  </div>
                  <Badge
                    variant="outline"
                    className="shrink-0 text-[10px] px-1.5 py-0 font-normal text-muted-foreground/60 border-muted-foreground/20"
                  >
                    {sourceLabel(t.source)}
                  </Badge>
                  {inDraft ? (
                    <Badge variant="secondary" className="shrink-0 gap-1">
                      <Check className="h-3 w-3" />
                      in draft
                    </Badge>
                  ) : (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onAdd(t)}
                      aria-label="Add to draft"
                    >
                      <Plus className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </ResizableList>
    </div>
  );
}
```

- [ ] **Step 2: Build verify**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/playlist/PlaylistInternalSearch.tsx
git commit -m "feat(playlist): add PlaylistInternalSearch tab component"
```

---

## Task 6: Implement `PlaylistOnlineSearch` component

**Files:**
- Create: `frontend/src/components/playlist/PlaylistOnlineSearch.tsx`

- [ ] **Step 1: Write the component**

Write `frontend/src/components/playlist/PlaylistOnlineSearch.tsx`:

```tsx
import { useState, useEffect } from "react";
import { Check, ListMusic, Loader2, Music, Plus, Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ResizableList } from "../ResizableList";
import type { SearchResult, Track } from "../../types";

interface Props {
  loading: boolean;
  error: string;
  results: SearchResult[];
  playlistName: string | null;
  onSearch: (q: string) => void;
  draftUrls: Set<string>;
  onAdd: (track: Track) => void;
  onAddAll: (tracks: Track[]) => void;
  formatDuration: (s: number) => string;
}

const DEBOUNCE_MS = 1000;

function resultToTrack(r: SearchResult): Track {
  return {
    title: r.title,
    artist: r.artist,
    url: r.url,
    thumbnail: r.thumbnail,
    duration: r.duration || 0,
    requestedBy: "Web User",
  };
}

export function PlaylistOnlineSearch({
  loading,
  error,
  results,
  playlistName,
  onSearch,
  draftUrls,
  onAdd,
  onAddAll,
  formatDuration,
}: Props) {
  const [query, setQuery] = useState("");

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) return;
    const t = setTimeout(() => onSearch(trimmed), DEBOUNCE_MS);
    return () => clearTimeout(t);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const addableCount = results.filter((r) => !draftUrls.has(r.url)).length;

  return (
    <div className="space-y-2">
      <div className="relative">
        <Search className="absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search YouTube / SoundCloud / Bandcamp, or paste a URL…"
          className="pl-8 pr-8"
        />
        {loading && (
          <Loader2 className="absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-muted-foreground" />
        )}
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {playlistName && results.length > 0 && (
        <div className="flex items-center gap-2 rounded-md bg-primary/8 border border-primary/20 px-3 py-2 text-sm">
          <ListMusic className="h-4 w-4 shrink-0 text-primary" />
          <span className="font-medium text-primary truncate flex-1">{playlistName}</span>
          <span className="text-xs text-muted-foreground shrink-0">{results.length} tracks</span>
          <Button
            size="sm"
            disabled={addableCount === 0}
            onClick={() =>
              onAddAll(results.filter((r) => !draftUrls.has(r.url)).map(resultToTrack))
            }
          >
            <Plus className="mr-1 h-3 w-3" />
            Add all ({addableCount})
          </Button>
        </div>
      )}

      <ResizableList
        defaultHeight={200}
        minHeight={80}
        maxHeight={400}
        className="rounded-md border"
      >
        {!loading && results.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-6 text-muted-foreground">
            <Music className="mb-1 h-6 w-6" />
            <p className="text-sm">
              {query.trim() ? "No results yet." : "Type to search the web."}
            </p>
          </div>
        ) : (
          <ul className="p-1 space-y-0.5">
            {results.map((r) => {
              const inDraft = draftUrls.has(r.url);
              return (
                <li
                  key={r.videoId}
                  className="flex items-center gap-2 rounded-md p-2 hover:bg-muted/50"
                >
                  {r.thumbnail ? (
                    <img src={r.thumbnail} alt="" className="h-7 w-7 rounded object-cover shrink-0" />
                  ) : (
                    <div className="flex h-7 w-7 items-center justify-center rounded bg-muted shrink-0">
                      <Music className="h-3 w-3 text-muted-foreground" />
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <p className="truncate text-sm font-medium">{r.title}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      {r.artist}
                      {r.duration > 0 && ` — ${formatDuration(r.duration)}`}
                    </p>
                  </div>
                  {inDraft ? (
                    <Badge variant="secondary" className="shrink-0 gap-1">
                      <Check className="h-3 w-3" />
                      in draft
                    </Badge>
                  ) : (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => onAdd(resultToTrack(r))}
                      aria-label="Add to draft"
                    >
                      <Plus className="h-3.5 w-3.5" />
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </ResizableList>
    </div>
  );
}
```

- [ ] **Step 2: Build verify**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/playlist/PlaylistOnlineSearch.tsx
git commit -m "feat(playlist): add PlaylistOnlineSearch tab component"
```

---

## Task 7: Implement `PlaylistEditDialog` (orchestrator)

**Files:**
- Create: `frontend/src/components/playlist/PlaylistEditDialog.tsx`

- [ ] **Step 1: Write the dialog**

Write `frontend/src/components/playlist/PlaylistEditDialog.tsx`:

```tsx
import { useState, useEffect, useMemo } from "react";
import {
  doc,
  getDoc,
  writeBatch,
  serverTimestamp,
} from "firebase/firestore";
import { db } from "../../firebase";
import type { Track, CurrentTrack, SearchResult } from "../../types";
import type { PlaylistDoc, UnifiedTrack } from "./types";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Loader2, Save } from "lucide-react";
import { useUnifiedTrackPool } from "./hooks/useUnifiedTrackPool";
import { useBotSearch } from "./hooks/useBotSearch";
import { PlaylistDraftList } from "./PlaylistDraftList";
import { PlaylistInternalSearch } from "./PlaylistInternalSearch";
import { PlaylistOnlineSearch } from "./PlaylistOnlineSearch";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  serverId: string;
  currentQueue: Track[];
  currentTrack: CurrentTrack | null;
  searchResults?: SearchResult[];
  searchQuery?: string | null;
  searchPlaylistName?: string | null;
  /** When set, dialog is in edit mode for this playlist. */
  editing: PlaylistDoc | null;
  onSaved: () => void;
}

type Tab = "internal" | "online";

const formatDuration = (s: number) => {
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
};

function unifiedToTrack(u: UnifiedTrack): Track {
  return {
    title: u.title,
    artist: u.artist,
    url: u.url,
    thumbnail: u.thumbnail,
    duration: u.duration,
    requestedBy: u.requestedBy,
  };
}

export function PlaylistEditDialog({
  open,
  onOpenChange,
  serverId,
  currentQueue,
  currentTrack,
  searchResults,
  searchQuery,
  searchPlaylistName,
  editing,
  onSaved,
}: Props) {
  const [name, setName] = useState("");
  const [draft, setDraft] = useState<Track[]>([]);
  const [tab, setTab] = useState<Tab>("internal");
  const [filter, setFilter] = useState("");
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);

  // Reset on open
  useEffect(() => {
    if (!open) return;
    setName(editing?.name ?? "");
    setDraft(editing?.tracks ?? []);
    setTab("internal");
    setFilter("");
    setSaveError("");
    setSaving(false);
  }, [open, editing]);

  const { pool, loading: poolLoading } = useUnifiedTrackPool({
    serverId,
    currentQueue,
    currentTrack,
    editingPlaylistName: editing?.name,
    existingTracks: editing?.tracks,
  });

  const botSearch = useBotSearch({
    serverId,
    searchResults,
    searchQuery,
    searchPlaylistName,
  });

  const draftUrls = useMemo(() => new Set(draft.map((t) => t.url)), [draft]);

  function addOne(track: Track) {
    if (draftUrls.has(track.url)) return;
    setDraft((d) => [track, ...d]);
  }

  function addMany(tracks: Track[]) {
    const existing = new Set(draft.map((t) => t.url));
    const fresh = tracks.filter((t) => !existing.has(t.url));
    if (fresh.length === 0) return;
    setDraft((d) => [...fresh, ...d]);
  }

  async function save() {
    const newName = name.trim();
    if (!newName || draft.length === 0) return;

    const isCreate = !editing;
    const isRename = !!editing && newName !== editing.name;

    setSaving(true);
    setSaveError("");

    try {
      if (isCreate || isRename) {
        const existing = await getDoc(
          doc(db, "servers", serverId, "playlists", newName)
        );
        if (existing.exists()) {
          setSaveError(`A playlist named "${newName}" already exists.`);
          setSaving(false);
          return;
        }
      }

      const payload = {
        name: newName,
        tracks: draft,
        createdBy: editing?.createdBy ?? "Web User",
        createdAt: editing?.createdAt ?? serverTimestamp(),
      };

      const batch = writeBatch(db);
      batch.set(doc(db, "servers", serverId, "playlists", newName), payload);
      if (isRename && editing) {
        batch.delete(doc(db, "servers", serverId, "playlists", editing.name));
      }
      await batch.commit();

      onSaved();
      onOpenChange(false);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Save failed.";
      setSaveError(msg);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>{editing ? "Edit Playlist" : "Create Playlist"}</DialogTitle>
          <DialogDescription>
            {editing
              ? "Reorder, add, or remove tracks. Rename allowed."
              : "Build a playlist from your queue, history, other playlists, or the web."}
          </DialogDescription>
        </DialogHeader>

        <Input
          type="text"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setSaveError("");
          }}
          placeholder="Playlist name"
          autoFocus
        />

        {/* Draft list */}
        <div className="space-y-1">
          <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            Playlist ({draft.length})
          </p>
          <PlaylistDraftList draft={draft} onChange={setDraft} formatDuration={formatDuration} />
        </div>

        {/* Search tabs */}
        <div className="flex flex-col flex-1 min-h-0 space-y-2">
          <div className="flex border-b">
            <button
              type="button"
              onClick={() => setTab("internal")}
              className={`px-3 py-1.5 text-sm font-medium border-b-2 -mb-px ${
                tab === "internal"
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              Internal
            </button>
            <button
              type="button"
              onClick={() => setTab("online")}
              className={`px-3 py-1.5 text-sm font-medium border-b-2 -mb-px ${
                tab === "online"
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              Online
            </button>
          </div>

          {tab === "internal" ? (
            <PlaylistInternalSearch
              pool={pool}
              poolLoading={poolLoading}
              filter={filter}
              onFilterChange={setFilter}
              draftUrls={draftUrls}
              onAdd={(u) => addOne(unifiedToTrack(u))}
              formatDuration={formatDuration}
            />
          ) : (
            <PlaylistOnlineSearch
              loading={botSearch.loading}
              error={botSearch.error}
              results={botSearch.results}
              playlistName={botSearch.playlistName}
              onSearch={botSearch.search}
              draftUrls={draftUrls}
              onAdd={addOne}
              onAddAll={addMany}
              formatDuration={formatDuration}
            />
          )}
        </div>

        {saveError && <p className="text-sm text-destructive">{saveError}</p>}

        <DialogFooter>
          <DialogClose render={<Button variant="outline" />}>Cancel</DialogClose>
          <Button
            onClick={save}
            disabled={!name.trim() || draft.length === 0 || saving}
          >
            {saving ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <Save className="mr-1 h-4 w-4" />
            )}
            Save ({draft.length})
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 2: Build verify**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/playlist/PlaylistEditDialog.tsx
git commit -m "feat(playlist): add PlaylistEditDialog orchestrator"
```

---

## Task 8: Slim down `PlaylistManager` to use the new dialog

**Files:**
- Modify: `frontend/src/components/PlaylistManager.tsx` (full rewrite — removes ~300 lines of dialog/import logic)

- [ ] **Step 1: Replace PlaylistManager with the slimmed version**

Replace the entire contents of `frontend/src/components/PlaylistManager.tsx` with:

```tsx
import { useState, useEffect, useCallback } from "react";
import {
  collection,
  getDocs,
  doc,
  deleteDoc,
  updateDoc,
} from "firebase/firestore";
import { db } from "../firebase";
import type { Track, CurrentTrack, SearchResult } from "../types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ResizableList } from "./ResizableList";
import {
  Library,
  Trash2,
  ChevronDown,
  ChevronRight,
  Music,
  Plus,
  ListPlus,
  Replace,
  Pencil,
} from "lucide-react";
import { PlaylistEditDialog } from "./playlist/PlaylistEditDialog";
import type { PlaylistDoc } from "./playlist/types";

interface Props {
  serverId: string;
  currentQueue: Track[];
  currentTrack: CurrentTrack | null;
  searchResults?: SearchResult[];
  searchQuery?: string | null;
  searchPlaylistName?: string | null;
}

export function PlaylistManager({
  serverId,
  currentQueue,
  currentTrack,
  searchResults,
  searchQuery,
  searchPlaylistName,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [playlists, setPlaylists] = useState<PlaylistDoc[]>([]);
  const [openPlaylist, setOpenPlaylist] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<PlaylistDoc | null>(null);

  const fetchPlaylists = useCallback(async () => {
    const snap = await getDocs(collection(db, "servers", serverId, "playlists"));
    setPlaylists(
      snap.docs.map((d) => ({ name: d.id, ...d.data() } as PlaylistDoc))
    );
  }, [serverId]);

  useEffect(() => {
    fetchPlaylists();
  }, [fetchPlaylists]);

  useEffect(() => {
    if (!expanded) setOpenPlaylist(null);
  }, [expanded]);

  function openCreate() {
    setEditing(null);
    setDialogOpen(true);
  }

  function openEdit(p: PlaylistDoc) {
    setEditing(p);
    setDialogOpen(true);
  }

  async function loadPlaylist(p: PlaylistDoc, mode: "add" | "replace") {
    const tracks = p.tracks.map((t) => ({ ...t, requestedBy: "Web User" }));
    if (mode === "replace") {
      await updateDoc(doc(db, "servers", serverId), { queue: tracks });
    } else {
      await updateDoc(doc(db, "servers", serverId), {
        queue: [...currentQueue, ...tracks],
      });
    }
  }

  async function deletePlaylist(name: string) {
    await deleteDoc(doc(db, "servers", serverId, "playlists", name));
    if (openPlaylist === name) setOpenPlaylist(null);
    fetchPlaylists();
  }

  const formatDuration = (s: number) => {
    const m = Math.floor(s / 60);
    return `${m}:${String(s % 60).padStart(2, "0")}`;
  };

  return (
    <>
      <Card>
        <Collapsible open={expanded} onOpenChange={setExpanded}>
          <CardHeader className="pb-3">
            <CollapsibleTrigger className="flex w-full items-center gap-2">
              <CardTitle className="flex items-center gap-2 text-lg">
                <Library className="h-4 w-4" />
                Playlists
              </CardTitle>
              <Badge variant="secondary" className="ml-auto mr-2">
                {playlists.length}
              </Badge>
              <ChevronDown
                className={`h-4 w-4 text-muted-foreground transition-transform ${expanded ? "rotate-180" : ""}`}
              />
            </CollapsibleTrigger>
          </CardHeader>
          <CollapsibleContent>
            <CardContent className="space-y-3 pt-0">
              <Button size="sm" variant="outline" onClick={openCreate}>
                <Plus className="mr-1 h-3 w-3" />
                Create Playlist
              </Button>

              {playlists.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-4 text-muted-foreground">
                  <Music className="mb-1 h-6 w-6" />
                  <p className="text-sm">No saved playlists yet.</p>
                </div>
              ) : (
                <ul className="space-y-1">
                  {playlists.map((p) => {
                    const isOpen = openPlaylist === p.name;
                    return (
                      <li key={p.name}>
                        <div
                          className="flex items-center gap-2 rounded-md p-2 hover:bg-muted/50 transition-colors cursor-pointer"
                          onClick={() => setOpenPlaylist(isOpen ? null : p.name)}
                        >
                          {isOpen ? (
                            <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
                          ) : (
                            <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                          )}
                          <div className="flex-1 min-w-0">
                            <span className="text-sm font-medium">{p.name}</span>
                            <span className="ml-2 text-xs text-muted-foreground">
                              {p.tracks.length} tracks · {p.createdBy}
                            </span>
                          </div>
                          <div className="flex gap-1" onClick={(e) => e.stopPropagation()}>
                            <Button size="sm" variant="ghost" title="Edit playlist" onClick={() => openEdit(p)}>
                              <Pencil className="h-3.5 w-3.5" />
                            </Button>
                            <Button size="sm" variant="ghost" title="Add to queue" onClick={() => loadPlaylist(p, "add")}>
                              <ListPlus className="h-3.5 w-3.5" />
                            </Button>
                            <Button size="sm" variant="ghost" title="Replace queue" onClick={() => loadPlaylist(p, "replace")}>
                              <Replace className="h-3.5 w-3.5" />
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              className="text-destructive hover:text-destructive"
                              onClick={() => deletePlaylist(p.name)}
                            >
                              <Trash2 className="h-3 w-3" />
                            </Button>
                          </div>
                        </div>
                        {isOpen && (
                          <ResizableList defaultHeight={200} minHeight={80} maxHeight={400}>
                            <ul className="ml-6 space-y-0.5">
                              {p.tracks.map((t, i) => (
                                <li
                                  key={`${t.url}-${i}`}
                                  className="flex items-center gap-2 rounded p-1.5"
                                >
                                  <span className="w-5 text-right text-xs text-muted-foreground/60">
                                    {i + 1}.
                                  </span>
                                  {t.thumbnail ? (
                                    <img
                                      src={t.thumbnail}
                                      alt=""
                                      className="h-7 w-7 rounded object-cover shrink-0"
                                    />
                                  ) : (
                                    <div className="flex h-7 w-7 items-center justify-center rounded bg-muted shrink-0">
                                      <Music className="h-3 w-3 text-muted-foreground" />
                                    </div>
                                  )}
                                  <div className="flex-1 min-w-0">
                                    <p className="truncate text-sm">{t.title}</p>
                                    <p className="truncate text-xs text-muted-foreground">
                                      {t.artist}
                                      {t.duration > 0 && ` — ${formatDuration(t.duration)}`}
                                    </p>
                                  </div>
                                </li>
                              ))}
                            </ul>
                          </ResizableList>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </CardContent>
          </CollapsibleContent>
        </Collapsible>
      </Card>

      <PlaylistEditDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        serverId={serverId}
        currentQueue={currentQueue}
        currentTrack={currentTrack}
        searchResults={searchResults}
        searchQuery={searchQuery}
        searchPlaylistName={searchPlaylistName}
        editing={editing}
        onSaved={fetchPlaylists}
      />
    </>
  );
}
```

- [ ] **Step 2: Build verify**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/PlaylistManager.tsx
git commit -m "refactor(playlist): replace inline dialog with PlaylistEditDialog"
```

---

## Task 9: Pass `searchPlaylistName` from Dashboard to PlaylistManager

**Files:**
- Modify: `frontend/src/components/Dashboard.tsx:139` (add prop)

- [ ] **Step 1: Add the missing prop**

Open `frontend/src/components/Dashboard.tsx`. At line 139, locate:

```tsx
<PlaylistManager serverId={serverId} currentQueue={state.queue} currentTrack={state.currentTrack} searchResults={state.searchResults} searchQuery={state.searchQuery} />
```

Replace with:

```tsx
<PlaylistManager
  serverId={serverId}
  currentQueue={state.queue}
  currentTrack={state.currentTrack}
  searchResults={state.searchResults}
  searchQuery={state.searchQuery}
  searchPlaylistName={state.searchPlaylistName}
/>
```

- [ ] **Step 2: Build verify**

Run: `cd frontend && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/Dashboard.tsx
git commit -m "feat(dashboard): forward searchPlaylistName to PlaylistManager"
```

---

## Task 10: Update Firestore security rules

**Files:**
- Modify: `firestore.rules`

- [ ] **Step 1: Tighten the playlist write rule**

Open `firestore.rules`. Locate lines 12–19:

```
match /servers/{serverId} {
  allow read, write: if true;

  // Subcollections: playlists, history
  match /{subcollection}/{docId} {
    allow read, write: if true;
  }
}
```

Replace with:

```
match /servers/{serverId} {
  allow read, write: if true;

  // Subcollections: history (open writes — used by bot via admin SDK and web client)
  match /{subcollection}/{docId} {
    allow read, write: if true;
  }

  // Playlists — split create/update/delete so the rule layer can be tightened later.
  // Today both create and update are allowed; client-side getDoc + writeBatch is the
  // primary defense against name collisions. Delete is allowed for owner-initiated
  // remove and rename batches.
  match /playlists/{name} {
    allow read: if true;
    allow create: if true;
    allow update: if true;
    allow delete: if true;
  }
}
```

- [ ] **Step 2: Verify rules syntax (dry-run only — no deploy)**

If `firebase` CLI is installed locally:

Run: `firebase firestore:rules:check firestore.rules` (or open the Firebase console rules tab, paste, click "Save & test")
Expected: no syntax errors.

If not installed: skip — the rule will be validated on the next `firebase deploy`. The plan does not deploy rules; the implementer should deploy via the project's normal release path.

- [ ] **Step 3: Commit**

```bash
git add firestore.rules
git commit -m "chore(rules): split playlist write rule into create/update/delete"
```

---

## Task 11: Manual QA pass

**Files:** none (manual testing only)

- [ ] **Step 1: Start the dev environment**

In one terminal: start the bot (so the Online tab can return real results).
```bash
cd bot && python main.py
```
In another: start the frontend.
```bash
cd frontend && npm run dev
```
Open the printed URL in a browser, sign in, and activate a server with at least 5 tracks already in queue and music history.

- [ ] **Step 2: Run the spec smoke checklist**

For each item, verify the behavior matches. If any fails, fix and re-run before continuing.

1. **Create with internal search** — open Create dialog → Internal tab → filter for a track → click `+` → drag to reorder → name "Test1" → Save. Verify the playlist appears in the list with the expected order.
2. **Create with online search** — open Create dialog → Online tab → type a query → wait for results → `+` two tracks → name "Test2" → Save. Verify saved correctly. Stop the bot, search again, verify "Search timed out" appears after ~15s.
3. **Edit existing — reorder** — click pencil on "Test1" → drag the bottom track to the top → Save. Verify the list shows the new order.
4. **Edit existing — add via internal** — open "Test1" edit → Internal tab → add a track from another playlist → Save. Verify the new track is at position 1.
5. **Duplicate-name on create** — open Create → name "Test1" → add any track → Save. Verify inline error "A playlist named \"Test1\" already exists." and no Firestore write.
6. **Rename to free name** — edit "Test1" → change name to "Renamed1" → Save. Verify "Test1" disappears from the list and "Renamed1" appears with the same tracks.
7. **Rename to taken name** — edit "Test2" → change name to "Renamed1" → Save. Verify inline error and no write.
8. **Already-in-draft signal** — open "Renamed1" edit → Internal tab → search for a track that's already in the draft. Verify the row shows a "✓ in draft" chip and there is no `+` button.
9. **Playlist URL paste in Online** — open Create → Online tab → paste a YouTube playlist URL → wait for results. Verify the banner shows the playlist name + count and an "Add all (N)" button. Click "Add all" → verify all tracks prepend to the draft.
10. **Concurrency** — open the global SearchPanel and start a search. Before it returns (within 1s of typing), open the edit dialog and start an Online search. Verify exactly one of the two shows results; the other shows a timeout. Neither one corrupts the other's state.
11. **Resize regions** — drag the resize handle below each region. Verify the height changes. Close + reopen → defaults restored.
12. **Mobile drag-to-reorder** — open in mobile viewport (Chrome devtools → Device Mode → iPhone). Long-press a row's grip and drag → verify reordering works on touch.

- [ ] **Step 3: Final tag commit**

If all 12 items pass:

```bash
git tag playlist-edit-redesign-v1
git push --tags
```

If any item fails: file a follow-up issue and fix before tagging.

---

## Self-review

**Spec coverage check:**
- Layout (sticky draft + tabbed search + resizable regions) → Tasks 4, 5, 6, 7
- Internal search (filter + 4-source dedupe pool, including other playlists) → Tasks 2, 5
- Online search (bot pipe + Add all + URL handling) → Tasks 3, 6
- Sortable draft with @dnd-kit → Task 4
- Save with create/rename/dup-check → Task 7
- Firestore rules update → Task 10
- Slim down PlaylistManager → Task 8
- Dashboard prop forwarding → Task 9
- Manual QA → Task 11

**Placeholder scan:** none found.

**Type/name consistency:** `editingPlaylistName` (hook arg) vs `editing.name` (dialog state) — different scopes, both correct. `addOne` / `addMany` consistent across Tasks 6, 7. `formatDuration` defined in Task 4 and Task 7 with the same signature; intentional duplication for component independence.
