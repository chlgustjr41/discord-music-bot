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
