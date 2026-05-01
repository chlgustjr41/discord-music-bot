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
