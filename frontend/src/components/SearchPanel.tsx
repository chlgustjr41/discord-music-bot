import { useState, useEffect, useRef } from "react";
import { doc, updateDoc, getDoc } from "firebase/firestore";
import { db } from "../firebase";
import type { SearchResult, Track } from "../types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ResizableList } from "./ResizableList";
import {
  Search,
  Loader2,
  Check,
  ArrowUpToLine,
  ArrowDownToLine,
  X,
} from "lucide-react";

interface Props {
  serverId: string;
  searchResults?: SearchResult[];
  searchQuery?: string | null;
}

export function SearchPanel({ serverId, searchResults, searchQuery }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [addedMsg, setAddedMsg] = useState("");
  const waitingForResults = useRef(false);

  // The bot clears searchQuery to null when it writes results back.
  // So the flow is:
  //   1. Frontend writes searchQuery="test", searchResults=[]
  //   2. Bot reads query, searches, writes searchResults=[...], searchQuery=null
  //   3. We detect: searchQuery became null AND searchResults has data → show results
  useEffect(() => {
    if (!waitingForResults.current) return;

    // searchQuery is still set = bot hasn't finished processing yet
    if (searchQuery) return;

    // searchQuery is null = bot finished. Check results.
    if (searchResults && searchResults.length > 0) {
      setResults(searchResults);
      setSelected(new Set());
      setLoading(false);
      waitingForResults.current = false;
    } else {
      // Bot cleared searchQuery but results are empty = no results found
      setResults([]);
      setLoading(false);
      setError("No results found.");
      waitingForResults.current = false;
    }
  }, [searchResults, searchQuery]);

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

  const handleSearch = async () => {
    const q = query.trim();
    if (!q) return;

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
    } catch {
      setError("Search failed.");
      setLoading(false);
      waitingForResults.current = false;
    }
  };

  const clearResults = () => {
    setResults([]);
    setSelected(new Set());
    setAddedMsg("");
    setError("");
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
        requestedBy: "Web User",
      }));

    if (tracks.length === 0) return;

    const snap = await getDoc(doc(db, "servers", serverId));
    const currentQueue: Track[] = snap.exists() ? snap.data().queue || [] : [];

    const newQueue =
      position === "top" ? [...tracks, ...currentQueue] : [...currentQueue, ...tracks];

    await updateDoc(doc(db, "servers", serverId), { queue: newQueue });

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

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-lg">
          <Search className="h-4 w-4" />
          Search / Add
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex gap-2">
          <Input
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setAddedMsg("");
            }}
            placeholder="Search by name, artist, or paste a YouTube link..."
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          />
          <Button onClick={handleSearch} disabled={loading || !query.trim()}>
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              "Search"
            )}
          </Button>
        </div>

        {addedMsg && <p className="text-xs text-primary">{addedMsg}</p>}
        {error && <p className="text-sm text-destructive">{error}</p>}

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
                      <div className="flex-1 min-w-0">
                        <p className="truncate text-sm font-medium">{r.title}</p>
                        <p className="truncate text-xs text-muted-foreground">
                          {r.artist}
                          {r.duration > 0 && ` — ${formatDuration(r.duration)}`}
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
    </Card>
  );
}
