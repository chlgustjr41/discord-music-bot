import { useState, useEffect, useCallback, useRef } from "react";
import {
  collection,
  getDocs,
  doc,
  setDoc,
  deleteDoc,
  updateDoc,
  serverTimestamp,
  query,
  orderBy,
  limit,
} from "firebase/firestore";
import { db } from "../firebase";
import type { Track, CurrentTrack, MusicHistoryEntry, SearchResult } from "../types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from "@/components/ui/dialog";
import { ResizableList } from "./ResizableList";
import {
  Library,
  Save,
  Trash2,
  ChevronDown,
  ChevronRight,
  Music,
  Check,
  Plus,
  ListPlus,
  Replace,
  Link,
  List,
  Loader2,
  Pencil,
} from "lucide-react";

interface Props {
  serverId: string;
  currentQueue: Track[];
  currentTrack: CurrentTrack | null;
  searchResults?: SearchResult[];
  searchQuery?: string | null;
}

interface PlaylistDoc {
  name: string;
  tracks: Track[];
  createdBy: string;
  createdAt: unknown;
}

interface UnifiedTrack {
  title: string;
  artist: string;
  url: string;
  thumbnail: string;
  duration: number;
  requestedBy: string;
  source: "queue" | "history" | "import" | "playlist";
}

type CreateMode = "select" | "import";

/* ------------------------------------------------------------------ */
/*  Main component                                                    */
/* ------------------------------------------------------------------ */

export function PlaylistManager({
  serverId,
  currentQueue,
  currentTrack,
  searchResults,
  searchQuery,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editingPlaylist, setEditingPlaylist] = useState<PlaylistDoc | null>(null);

  // --- Browse state ---
  const [playlists, setPlaylists] = useState<PlaylistDoc[]>([]);
  const [openPlaylist, setOpenPlaylist] = useState<string | null>(null);

  // --- Create dialog state ---
  const [createMode, setCreateMode] = useState<CreateMode>("select");
  const [playlistName, setPlaylistName] = useState("");
  const [selectedUrls, setSelectedUrls] = useState<Set<string>>(new Set());
  const [unifiedTracks, setUnifiedTracks] = useState<UnifiedTrack[]>([]);
  const [tracksLoading, setTracksLoading] = useState(false);

  // --- Import mode state ---
  const [importUrl, setImportUrl] = useState("");
  const [importLoading, setImportLoading] = useState(false);
  const [importError, setImportError] = useState("");
  const waitingForImport = useRef(false);

  /* ---- data fetching ---- */

  const fetchPlaylists = useCallback(async () => {
    const snap = await getDocs(
      collection(db, "servers", serverId, "playlists")
    );
    setPlaylists(
      snap.docs.map((d) => ({ name: d.id, ...d.data() } as PlaylistDoc))
    );
  }, [serverId]);

  // Preload playlists on mount so count is always correct
  useEffect(() => {
    fetchPlaylists();
  }, [fetchPlaylists]);

  useEffect(() => {
    if (!expanded) setOpenPlaylist(null);
  }, [expanded]);

  /** Build a single deduplicated track list from (existing playlist) + queue + history */
  const loadUnifiedTracks = useCallback(async (existingTracks?: Track[]) => {
    setTracksLoading(true);
    const seen = new Set<string>();
    const unified: UnifiedTrack[] = [];

    // Existing playlist tracks come first (when editing)
    if (existingTracks) {
      for (const t of existingTracks) {
        if (t.url && !seen.has(t.url)) {
          seen.add(t.url);
          unified.push({
            title: t.title, artist: t.artist, url: t.url,
            thumbnail: t.thumbnail, duration: t.duration,
            requestedBy: t.requestedBy, source: "playlist",
          });
        }
      }
    }

    if (currentTrack && !seen.has(currentTrack.url)) {
      seen.add(currentTrack.url);
      unified.push({
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
        unified.push({
          title: t.title, artist: t.artist, url: t.url,
          thumbnail: t.thumbnail, duration: t.duration,
          requestedBy: t.requestedBy, source: "queue",
        });
      }
    }

    try {
      const q = query(
        collection(db, "servers", serverId, "musicHistory"),
        orderBy("addedAt", "desc"),
        limit(100)
      );
      const snap = await getDocs(q);
      for (const d of snap.docs) {
        const h = d.data() as MusicHistoryEntry;
        if (!seen.has(h.url)) {
          seen.add(h.url);
          unified.push({
            title: h.title, artist: h.artist, url: h.url,
            thumbnail: h.thumbnail, duration: h.duration || 0,
            requestedBy: h.requestedBy || "Web User", source: "history",
          });
        }
      }
    } catch { /* proceed with queue tracks only */ }

    setUnifiedTracks(unified);
    setTracksLoading(false);
  }, [serverId, currentQueue, currentTrack]);

  /* ---- Watch for import results from bot ---- */
  useEffect(() => {
    if (!waitingForImport.current) return;
    if (searchQuery) return; // bot still processing

    // Bot finished — searchQuery cleared
    if (searchResults && searchResults.length > 0) {
      const imported: UnifiedTrack[] = searchResults.map((r) => ({
        title: r.title,
        artist: r.artist,
        url: r.url,
        thumbnail: r.thumbnail,
        duration: r.duration || 0,
        requestedBy: "Web User",
        source: "import" as const,
      }));
      setUnifiedTracks(imported);
      setSelectedUrls(new Set(imported.map((t) => t.url)));
      setImportLoading(false);
      setImportError("");
      waitingForImport.current = false;
    } else {
      setImportLoading(false);
      setImportError("No tracks found at this URL.");
      waitingForImport.current = false;
    }
  }, [searchResults, searchQuery]);

  // Import timeout
  useEffect(() => {
    if (!importLoading) return;
    const timeout = setTimeout(() => {
      if (waitingForImport.current) {
        setImportLoading(false);
        setImportError("Import timed out. Make sure the bot is connected.");
        waitingForImport.current = false;
      }
    }, 20000);
    return () => clearTimeout(timeout);
  }, [importLoading]);

  /* ---- open create dialog ---- */

  const openCreateDialog = () => {
    setEditingPlaylist(null);
    setPlaylistName("");
    setSelectedUrls(new Set());
    setCreateMode("select");
    setImportUrl("");
    setImportError("");
    setImportLoading(false);
    setSaveError("");
    setSaving(false);
    waitingForImport.current = false;
    loadUnifiedTracks();
    setCreateOpen(true);
  };

  const openEditDialog = (playlist: PlaylistDoc) => {
    setEditingPlaylist(playlist);
    setPlaylistName(playlist.name);
    setCreateMode("select");
    setImportUrl("");
    setImportError("");
    setImportLoading(false);
    setSaveError("");
    setSaving(false);
    waitingForImport.current = false;
    // Pre-select existing playlist track URLs
    setSelectedUrls(new Set(playlist.tracks.map((t) => t.url).filter(Boolean)));
    loadUnifiedTracks(playlist.tracks);
    setCreateOpen(true);
  };

  /* ---- import URL handler ---- */

  const handleImport = async () => {
    const url = importUrl.trim();
    if (!url) return;

    setImportLoading(true);
    setImportError("");
    setUnifiedTracks([]);
    setSelectedUrls(new Set());
    waitingForImport.current = true;

    try {
      await updateDoc(doc(db, "servers", serverId), {
        searchQuery: url,
        searchResults: [],
      });
    } catch {
      setImportError("Failed to send import request.");
      setImportLoading(false);
      waitingForImport.current = false;
    }
  };

  /* ---- mode switch ---- */

  const switchMode = (mode: CreateMode) => {
    setCreateMode(mode);
    setSelectedUrls(new Set());
    setImportError("");
    waitingForImport.current = false;
    setImportLoading(false);

    if (mode === "select") {
      loadUnifiedTracks();
    } else {
      setUnifiedTracks([]);
      setImportUrl("");
    }
  };

  /* ---- selection helpers ---- */

  const toggleUrl = (url: string) => {
    setSelectedUrls((prev) => {
      const next = new Set(prev);
      if (next.has(url)) next.delete(url);
      else next.add(url);
      return next;
    });
  };

  const toggleAll = () => {
    if (selectedUrls.size === unifiedTracks.length) {
      setSelectedUrls(new Set());
    } else {
      setSelectedUrls(new Set(unifiedTracks.map((t) => t.url)));
    }
  };

  /* ---- actions ---- */

  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);

  const savePlaylist = async () => {
    if (!playlistName.trim() || selectedUrls.size === 0) return;
    const tracks: Track[] = unifiedTracks
      .filter((t) => selectedUrls.has(t.url))
      .map(({ title, artist, url, thumbnail, duration, requestedBy }) => ({
        title: title || "Unknown",
        artist: artist || "",
        url: url || "",
        thumbnail: thumbnail || "",
        duration: duration || 0,
        requestedBy: requestedBy || "Web User",
      }));
    if (tracks.length === 0) return;

    setSaving(true);
    setSaveError("");
    try {
      await setDoc(
        doc(db, "servers", serverId, "playlists", playlistName.trim()),
        { name: playlistName.trim(), tracks, createdBy: "Web User", createdAt: serverTimestamp() }
      );
      setCreateOpen(false);
      await fetchPlaylists();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Save failed";
      setSaveError(msg);
    } finally {
      setSaving(false);
    }
  };

  const loadPlaylist = async (playlist: PlaylistDoc, mode: "add" | "replace") => {
    const tracksToLoad = playlist.tracks.map((t) => ({ ...t, requestedBy: "Web User" }));
    if (mode === "replace") {
      await updateDoc(doc(db, "servers", serverId), { queue: tracksToLoad });
    } else {
      await updateDoc(doc(db, "servers", serverId), { queue: [...currentQueue, ...tracksToLoad] });
    }
  };

  const deletePlaylist = async (name: string) => {
    await deleteDoc(doc(db, "servers", serverId, "playlists", name));
    if (openPlaylist === name) setOpenPlaylist(null);
    fetchPlaylists();
  };

  const formatDuration = (s: number) => {
    const m = Math.floor(s / 60);
    return `${m}:${String(s % 60).padStart(2, "0")}`;
  };

  /* ---- render ---- */

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
              <Button size="sm" variant="outline" onClick={openCreateDialog}>
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
                            <Button size="sm" variant="ghost" title="Edit playlist" onClick={() => openEditDialog(p)}>
                              <Pencil className="h-3.5 w-3.5" />
                            </Button>
                            <Button size="sm" variant="ghost" title="Add to queue" onClick={() => loadPlaylist(p, "add")}>
                              <ListPlus className="h-3.5 w-3.5" />
                            </Button>
                            <Button size="sm" variant="ghost" title="Replace queue" onClick={() => loadPlaylist(p, "replace")}>
                              <Replace className="h-3.5 w-3.5" />
                            </Button>
                            <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive" onClick={() => deletePlaylist(p.name)}>
                              <Trash2 className="h-3 w-3" />
                            </Button>
                          </div>
                        </div>
                        {isOpen && (
                          <ResizableList defaultHeight={200} minHeight={80} maxHeight={400}>
                            <ul className="ml-6 space-y-0.5">
                              {p.tracks.map((t, i) => (
                                <li key={`${t.url}-${i}`} className="flex items-center gap-2 rounded p-1.5">
                                  <span className="w-5 text-right text-xs text-muted-foreground/60">{i + 1}.</span>
                                  {t.thumbnail ? (
                                    <img src={t.thumbnail} alt="" className="h-7 w-7 rounded object-cover shrink-0" />
                                  ) : (
                                    <div className="flex h-7 w-7 items-center justify-center rounded bg-muted shrink-0">
                                      <Music className="h-3 w-3 text-muted-foreground" />
                                    </div>
                                  )}
                                  <div className="flex-1 min-w-0">
                                    <p className="truncate text-sm">{t.title}</p>
                                    <p className="truncate text-xs text-muted-foreground">
                                      {t.artist}{t.duration > 0 && ` — ${formatDuration(t.duration)}`}
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

      {/* ============ CREATE DIALOG ============ */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="sm:max-w-lg max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>{editingPlaylist ? "Edit Playlist" : "Create Playlist"}</DialogTitle>
            <DialogDescription>
              {editingPlaylist
                ? "Add or remove tracks. Existing tracks are pre-selected."
                : createMode === "select"
                  ? "Select tracks from your queue and music history."
                  : "Import tracks from a YouTube playlist URL."}
            </DialogDescription>
          </DialogHeader>

          {/* Mode toggle (create only) */}
          {!editingPlaylist && (
            <div className="flex gap-1 p-1 rounded-lg bg-muted">
              <button
                onClick={() => switchMode("select")}
                className={`flex-1 flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  createMode === "select"
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <List className="h-3.5 w-3.5" />
                Select Tracks
              </button>
              <button
                onClick={() => switchMode("import")}
                className={`flex-1 flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  createMode === "import"
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <Link className="h-3.5 w-3.5" />
                Import URL
              </button>
            </div>
          )}

          {/* Name input */}
          <Input
            type="text"
            value={playlistName}
            onChange={(e) => { if (!editingPlaylist) setPlaylistName(e.target.value); }}
            placeholder="Playlist name"
            onKeyDown={(e) => { if (e.key === "Enter") savePlaylist(); }}
            readOnly={!!editingPlaylist}
            className={editingPlaylist ? "opacity-60" : ""}
            autoFocus={!editingPlaylist}
          />

          {/* Import URL input (import mode only) */}
          {createMode === "import" && (
            <div className="flex gap-2">
              <Input
                type="text"
                value={importUrl}
                onChange={(e) => { setImportUrl(e.target.value); setImportError(""); }}
                placeholder="Paste YouTube playlist URL..."
                onKeyDown={(e) => { if (e.key === "Enter") handleImport(); }}
              />
              <Button
                onClick={handleImport}
                disabled={importLoading || !importUrl.trim()}
                variant="secondary"
              >
                {importLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  "Fetch"
                )}
              </Button>
            </div>
          )}

          {importError && (
            <p className="text-sm text-destructive">{importError}</p>
          )}

          {/* Selection toolbar */}
          {unifiedTracks.length > 0 && (
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={toggleAll}
              >
                {selectedUrls.size === unifiedTracks.length
                  ? "Deselect All"
                  : "Select All"}
              </Button>
              {selectedUrls.size > 0 && (
                <>
                  <Badge variant="secondary">
                    {selectedUrls.size} / {unifiedTracks.length}
                  </Badge>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-xs"
                    onClick={() => setSelectedUrls(new Set())}
                  >
                    Clear
                  </Button>
                </>
              )}
            </div>
          )}

          {/* Track list */}
          <div className="flex-1 min-h-0 overflow-y-auto rounded-md border">
            {tracksLoading || importLoading ? (
              <div className="flex items-center justify-center py-8 text-muted-foreground gap-2">
                <Loader2 className="h-4 w-4 animate-spin" />
                <p className="text-sm">
                  {importLoading ? "Fetching playlist..." : "Loading tracks..."}
                </p>
              </div>
            ) : unifiedTracks.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
                <Music className="mb-1 h-6 w-6" />
                <p className="text-sm">
                  {createMode === "import"
                    ? "Paste a YouTube playlist URL and click Fetch."
                    : "No tracks available. Play some music first!"}
                </p>
              </div>
            ) : (
              <ul className="p-1 space-y-0.5">
                {unifiedTracks.map((t) => {
                  const isSelected = selectedUrls.has(t.url);
                  return (
                    <li
                      key={t.url}
                      onClick={() => toggleUrl(t.url)}
                      className={`flex items-center gap-3 rounded-md p-2 cursor-pointer transition-colors ${
                        isSelected
                          ? "bg-primary/10 border border-primary/30"
                          : "hover:bg-muted/50"
                      }`}
                    >
                      <div
                        className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border ${
                          isSelected
                            ? "border-primary bg-primary text-primary-foreground"
                            : "border-muted-foreground/30"
                        }`}
                      >
                        {isSelected && <Check className="h-3 w-3" />}
                      </div>
                      {t.thumbnail ? (
                        <img src={t.thumbnail} alt="" className="h-8 w-8 rounded object-cover shrink-0" />
                      ) : (
                        <div className="flex h-8 w-8 items-center justify-center rounded bg-muted shrink-0">
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
                      {createMode === "select" && (
                        <Badge
                          variant="outline"
                          className="shrink-0 text-[10px] px-1.5 py-0 font-normal text-muted-foreground/60 border-muted-foreground/20"
                        >
                          {t.source === "playlist" ? "Playlist" : t.source === "queue" ? "Queue" : "History"}
                        </Badge>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {/* Footer */}
          {saveError && (
            <p className="text-sm text-destructive">{saveError}</p>
          )}
          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>
              Cancel
            </DialogClose>
            <Button
              onClick={savePlaylist}
              disabled={!playlistName.trim() || selectedUrls.size === 0 || saving}
            >
              {saving ? (
                <Loader2 className="mr-1 h-4 w-4 animate-spin" />
              ) : (
                <Save className="mr-1 h-4 w-4" />
              )}
              Save ({selectedUrls.size})
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
