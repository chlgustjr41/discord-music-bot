import { useState, useEffect } from "react";
import {
  collection,
  query,
  orderBy,
  limit,
  onSnapshot,
  doc,
  updateDoc,
  getDoc,
  deleteDoc,
} from "firebase/firestore";
import { db } from "../firebase";
import { getIdentityName } from "../lib/identity";
import { useSharedSection } from "../hooks/useSharedSection";
import type { MusicHistoryEntry, Track } from "../types";
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
  Music,
  ChevronDown,
  Clock,
  Check,
  Trash2,
  ArrowUpToLine,
  ArrowDownToLine,
} from "lucide-react";

interface Props {
  serverId: string;
}

export function MusicHistory({ serverId }: Props) {
  const [tracks, setTracks] = useState<MusicHistoryEntry[]>([]);
  const [expanded, setExpanded] = useSharedSection("music", false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [actionMsg, setActionMsg] = useState("");

  useEffect(() => {
    if (!expanded) return;
    const q = query(
      collection(db, "servers", serverId, "musicHistory"),
      orderBy("addedAt", "desc"),
      limit(50)
    );
    const unsub = onSnapshot(q, (snap) => {
      setTracks(
        snap.docs.map(
          (d) => ({ id: d.id, ...d.data() } as MusicHistoryEntry)
        )
      );
    });
    return () => unsub();
  }, [expanded, serverId]);

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => {
    if (selected.size === tracks.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(tracks.map((t) => t.id)));
    }
  };

  const deleteSelected = async () => {
    const ids = Array.from(selected);
    for (const id of ids) {
      await deleteDoc(doc(db, "servers", serverId, "musicHistory", id));
    }
    setSelected(new Set());
    showMsg(`Deleted ${ids.length} track${ids.length > 1 ? "s" : ""}`);
  };

  const addToQueue = async (position: "top" | "bottom") => {
    const selectedTracks: Track[] = tracks
      .filter((t) => selected.has(t.id))
      .map((t) => ({
        title: t.title,
        artist: t.artist,
        url: t.url,
        thumbnail: t.thumbnail,
        duration: t.duration || 0,
        requestedBy: getIdentityName(),
      }));

    if (selectedTracks.length === 0) return;

    const snap = await getDoc(doc(db, "servers", serverId));
    const currentQueue: Track[] = snap.exists()
      ? snap.data().queue || []
      : [];

    const newQueue =
      position === "top"
        ? [...selectedTracks, ...currentQueue]
        : [...currentQueue, ...selectedTracks];

    await updateDoc(doc(db, "servers", serverId), { queue: newQueue });

    setSelected(new Set());
    const count = selectedTracks.length;
    showMsg(
      `Added ${count} track${count > 1 ? "s" : ""} to ${position} of queue`
    );
  };

  const showMsg = (msg: string) => {
    setActionMsg(msg);
    setTimeout(() => setActionMsg(""), 3000);
  };

  const formatDuration = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${String(secs).padStart(2, "0")}`;
  };

  const formatTime = (ts: unknown) => {
    if (!ts) return "";
    try {
      // Firestore Timestamp objects have a toDate() method
      const d =
        ts && typeof ts === "object" && "toDate" in ts
          ? (ts as { toDate: () => Date }).toDate()
          : new Date(ts as string);
      return d.toLocaleDateString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return "";
    }
  };

  return (
    <Card>
      <Collapsible open={expanded} onOpenChange={setExpanded}>
        <CardHeader className="pb-3">
          <CollapsibleTrigger className="flex w-full items-center gap-2">
            <CardTitle className="flex items-center gap-2 text-lg">
              <Music className="h-4 w-4" />
              Music History
            </CardTitle>
            <ChevronDown
              className={`ml-auto h-4 w-4 text-muted-foreground transition-transform ${expanded ? "rotate-180" : ""}`}
            />
          </CollapsibleTrigger>
        </CardHeader>
        <CollapsibleContent>
          <CardContent className="space-y-2 pt-0">
            {actionMsg && (
              <p className="text-xs text-primary">{actionMsg}</p>
            )}

            {tracks.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-4 text-muted-foreground">
                <Clock className="mb-1 h-6 w-6" />
                <p className="text-sm">No music history yet.</p>
              </div>
            ) : (
              <div className="space-y-2">
                {/* Selection toolbar */}
                <div className="flex items-center gap-2 flex-wrap">
                  <Button size="sm" variant="outline" onClick={selectAll}>
                    {selected.size === tracks.length
                      ? "Deselect All"
                      : "Select All"}
                  </Button>
                  {selected.size > 0 && (
                    <>
                      <Badge variant="secondary">
                        {selected.size} selected
                      </Badge>
                      <div className="ml-auto flex gap-1">
                        <Button
                          size="sm"
                          onClick={() => addToQueue("top")}
                        >
                          <ArrowUpToLine className="mr-1 h-3 w-3" />
                          Top
                        </Button>
                        <Button
                          size="sm"
                          onClick={() => addToQueue("bottom")}
                        >
                          <ArrowDownToLine className="mr-1 h-3 w-3" />
                          Bottom
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="text-destructive hover:text-destructive"
                          onClick={deleteSelected}
                        >
                          <Trash2 className="mr-1 h-3 w-3" />
                          Delete
                        </Button>
                      </div>
                    </>
                  )}
                </div>

                {/* Track list */}
                <ResizableList defaultHeight={384} minHeight={100}>
                  <ul className="space-y-1">
                    {tracks.map((t) => {
                      const isSelected = selected.has(t.id);
                      return (
                        <li
                          key={t.id}
                          onClick={() => toggleSelect(t.id)}
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
                          <div className="flex-1 min-w-0">
                            <p className="truncate text-sm font-medium">
                              {t.title}
                            </p>
                            <p className="truncate text-xs text-muted-foreground">
                              {t.artist}
                              {t.duration > 0 &&
                                ` — ${formatDuration(t.duration)}`}
                              {t.playCount > 1 && ` · ${t.playCount}x played`}
                              {" · "}
                              {t.requestedBy}
                              {t.addedAt ? ` · ${formatTime(t.addedAt)}` : null}
                            </p>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </ResizableList>
              </div>
            )}
          </CardContent>
        </CollapsibleContent>
      </Collapsible>
    </Card>
  );
}
