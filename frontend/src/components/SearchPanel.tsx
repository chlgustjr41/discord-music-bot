import { useState, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { doc, updateDoc, getDoc } from "firebase/firestore";
import { db } from "../firebase";
import { getIdentityName } from "../lib/identity";
import { bumpMemberStat } from "../lib/social";
import type { SearchResult, Track } from "../types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ResizableList } from "./ResizableList";
import { TrackRowSkeleton } from "./TrackRowSkeleton";
import {
  Search,
  Loader2,
  Check,
  ArrowUpToLine,
  ArrowDownToLine,
  X,
  Info,
  ListMusic,
} from "lucide-react";

interface Props {
  serverId: string;
  searchResults?: SearchResult[];
  searchQuery?: string | null;
  searchPlaylistName?: string | null;
}

const DEBOUNCE_MS = 200;

export function SearchPanel({ serverId, searchResults, searchQuery, searchPlaylistName }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [playlistName, setPlaylistName] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [addedMsg, setAddedMsg] = useState("");
  const [infoOpen, setInfoOpen] = useState(false);
  const [infoPos, setInfoPos] = useState({ top: 0, right: 0 });
  const infoButtonRef = useRef<HTMLButtonElement>(null);
  const waitingForResults = useRef(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSentQuery = useRef("");

  // Watch for bot search results coming back via Firestore
  useEffect(() => {
    if (!waitingForResults.current) return;
    if (searchQuery) return; // bot still processing

    if (searchResults && searchResults.length > 0) {
      const isPlaylist = !!searchPlaylistName;
      setResults(searchResults);
      setPlaylistName(searchPlaylistName ?? null);
      // Auto-select all tracks when results come from a playlist
      setSelected(isPlaylist ? new Set(searchResults.map((r) => r.videoId)) : new Set());
      setLoading(false);
      waitingForResults.current = false;
    } else {
      setResults([]);
      setPlaylistName(null);
      setLoading(false);
      setError("No results found.");
      waitingForResults.current = false;
    }
  }, [searchResults, searchQuery, searchPlaylistName]);

  // Timeout — if bot doesn't respond within 15s, stop loading
  useEffect(() => {
    if (!loading) return;
    const timeout = setTimeout(() => {
      if (waitingForResults.current) {
        setLoading(false);
        setError("Search timed out. Make sure the bot is connected.");
        waitingForResults.current = false;
      }
    }, 15000);
    return () => clearTimeout(timeout);
  }, [loading]);

  // Send search request to Firestore for the bot to pick up
  const fireSearch = async (q: string) => {
    if (q === lastSentQuery.current) return;
    lastSentQuery.current = q;

    setLoading(true);
    setError("");
    setResults([]);
    setSelected(new Set());
    setAddedMsg("");
    waitingForResults.current = true;

    try {
      await updateDoc(doc(db, "servers", serverId), {
        searchQuery: q,
        searchResults: [],
      });
      bumpMemberStat(serverId, "searches");
    } catch {
      setError("Search failed.");
      setLoading(false);
      waitingForResults.current = false;
    }
  };

  // Debounce: fire search 1s after the user stops typing
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);

    const trimmed = query.trim();
    if (!trimmed) {
      setResults([]);
      setLoading(false);
      setError("");
      lastSentQuery.current = "";
      return;
    }

    debounceRef.current = setTimeout(() => {
      fireSearch(trimmed);
    }, DEBOUNCE_MS);

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, serverId]);

  const clearResults = () => {
    setQuery("");
    setResults([]);
    setPlaylistName(null);
    setSelected(new Set());
    setAddedMsg("");
    setError("");
    lastSentQuery.current = "";
  };

  const toggleSelect = (videoId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(videoId)) {
        next.delete(videoId);
      } else {
        next.add(videoId);
      }
      return next;
    });
  };

  const selectAll = () => {
    if (selected.size === results.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(results.map((r) => r.videoId)));
    }
  };

  const addSelected = async (position: "top" | "bottom") => {
    const tracks: Track[] = results
      .filter((r) => selected.has(r.videoId))
      .map((r) => ({
        title: r.title,
        artist: r.artist,
        url: r.url,
        thumbnail: r.thumbnail,
        duration: r.duration || 0,
        requestedBy: getIdentityName(),
      }));

    if (tracks.length === 0) return;

    const snap = await getDoc(doc(db, "servers", serverId));
    const currentQueue: Track[] = snap.exists() ? snap.data().queue || [] : [];

    const newQueue =
      position === "top" ? [...tracks, ...currentQueue] : [...currentQueue, ...tracks];

    await updateDoc(doc(db, "servers", serverId), { queue: newQueue });
    bumpMemberStat(serverId, "queueAdds", tracks.length);

    setResults((prev) => prev.filter((r) => !selected.has(r.videoId)));
    setSelected(new Set());
    const count = tracks.length;
    setAddedMsg(
      `Added ${count} track${count > 1 ? "s" : ""} to ${position} of queue`
    );
    setTimeout(() => setAddedMsg(""), 3000);
  };

  const formatDuration = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${String(secs).padStart(2, "0")}`;
  };

  // Source badge derived from the track URL (Spotify joins this list later).
  const sourceOf = (url: string): { label: string; className: string } => {
    if (/youtube\.com|youtu\.be/.test(url))
      return { label: "YouTube", className: "bg-red-500/15 text-red-500" };
    if (/soundcloud\.com/.test(url))
      return { label: "SoundCloud", className: "bg-orange-500/15 text-orange-500" };
    if (/bandcamp\.com/.test(url))
      return { label: "Bandcamp", className: "bg-teal-500/15 text-teal-500" };
    return { label: "Web", className: "bg-muted text-muted-foreground" };
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-lg">
          <Search className="h-4 w-4" />
          Search / Add
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="relative flex items-center gap-2">
          <div className="relative flex-1">
            <Input
              type="text"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setAddedMsg("");
              }}
              placeholder="Search or paste a YouTube / SoundCloud / Bandcamp link…"
              className="pr-8"
            />
            {loading && (
              <Loader2 className="absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-muted-foreground" />
            )}
          </div>
          {/* Supported formats info */}
          <div className="shrink-0">
            <button
              ref={infoButtonRef}
              type="button"
              onMouseEnter={() => {
                const rect = infoButtonRef.current?.getBoundingClientRect();
                if (rect) {
                  setInfoPos({ top: rect.bottom + 8, right: window.innerWidth - rect.right });
                }
                setInfoOpen(true);
              }}
              onMouseLeave={() => setInfoOpen(false)}
              onFocus={() => {
                const rect = infoButtonRef.current?.getBoundingClientRect();
                if (rect) {
                  setInfoPos({ top: rect.bottom + 8, right: window.innerWidth - rect.right });
                }
                setInfoOpen(true);
              }}
              onBlur={() => setInfoOpen(false)}
              className="flex h-9 w-9 items-center justify-center rounded-md border border-input bg-background text-muted-foreground hover:text-foreground transition-colors"
              aria-label="Supported search formats"
            >
              <Info className="h-4 w-4" />
            </button>
          </div>
          {infoOpen && createPortal(
            <div
              style={{ position: "fixed", top: infoPos.top, right: infoPos.right }}
              className="z-[9999] w-72 rounded-lg border bg-popover p-3 shadow-md text-xs text-popover-foreground space-y-2"
            >
              <p className="font-semibold text-sm">Supported formats</p>
              <div className="space-y-1.5">
                <div>
                  <span className="font-medium">Text search</span>
                  <p className="text-muted-foreground">Searches YouTube Music. Type a song name, artist, or both.</p>
                </div>
                <div>
                  <span className="font-medium">YouTube URL</span>
                  <p className="text-muted-foreground">Paste a video or playlist link (youtube.com / youtu.be).</p>
                </div>
                <div>
                  <span className="font-medium">SoundCloud URL</span>
                  <p className="text-muted-foreground">Paste a track, set, or artist page link (soundcloud.com).</p>
                </div>
                <div>
                  <span className="font-medium">Bandcamp URL</span>
                  <p className="text-muted-foreground">Paste an album or track link from any Bandcamp page.</p>
                </div>
              </div>
            </div>,
            document.body
          )}
        </div>

        {addedMsg && <p className="text-xs text-primary">{addedMsg}</p>}
        {error && <p className="text-sm text-destructive">{error}</p>}

        {/* Playlist banner */}
        {playlistName && results.length > 0 && (
          <div className="flex items-center gap-2 rounded-md bg-primary/8 border border-primary/20 px-3 py-2 text-sm">
            <ListMusic className="h-4 w-4 shrink-0 text-primary" />
            <span className="font-medium text-primary truncate">{playlistName}</span>
            <span className="text-muted-foreground shrink-0">— {results.length} tracks</span>
          </div>
        )}

        {loading && results.length === 0 && (
          <ResizableList defaultHeight={384} minHeight={100}>
            <TrackRowSkeleton count={6} />
          </ResizableList>
        )}

        {results.length > 0 && (
          <div className="space-y-2">
            {/* Selection toolbar */}
            <div className="flex items-center gap-2 flex-wrap">
              <Button size="sm" variant="outline" onClick={selectAll}>
                {selected.size === results.length ? "Deselect All" : "Select All"}
              </Button>
              <Button size="sm" variant="ghost" onClick={clearResults}>
                <X className="mr-1 h-3 w-3" />
                Clear
              </Button>
              {selected.size > 0 && (
                <>
                  <Badge variant="secondary">{selected.size} selected</Badge>
                  <div className="ml-auto flex gap-1">
                    <Button size="sm" onClick={() => addSelected("top")}>
                      <ArrowUpToLine className="mr-1 h-3 w-3" />
                      Top
                    </Button>
                    <Button size="sm" onClick={() => addSelected("bottom")}>
                      <ArrowDownToLine className="mr-1 h-3 w-3" />
                      Bottom
                    </Button>
                  </div>
                </>
              )}
            </div>

            {/* Results list */}
            <ResizableList defaultHeight={384} minHeight={100}>
              <ul className="space-y-1">
                {results.map((r) => {
                  const isSelected = selected.has(r.videoId);
                  return (
                    <li
                      key={r.videoId}
                      onClick={() => toggleSelect(r.videoId)}
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
                      {r.thumbnail && (
                        <img
                          src={r.thumbnail}
                          alt=""
                          loading="lazy"
                          className="h-9 w-16 shrink-0 rounded object-cover bg-muted"
                          onError={(e) => {
                            (e.target as HTMLImageElement).style.display = "none";
                          }}
                        />
                      )}
                      <div className="flex-1 min-w-0">
                        <p className="truncate text-sm font-medium">{r.title}</p>
                        <p className="truncate text-xs text-muted-foreground">
                          {r.artist}
                          {r.duration > 0 && ` — ${formatDuration(r.duration)}`}
                        </p>
                      </div>
                      <span
                        className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${sourceOf(r.url).className}`}
                      >
                        {sourceOf(r.url).label}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </ResizableList>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
