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

const DEBOUNCE_MS = 300;

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
    <div className="flex flex-1 flex-col min-h-0 space-y-2">
      <div className="relative">
        <Search className="pointer-events-none absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search YouTube / SoundCloud / Bandcamp, or paste a URL…"
          className="pl-8 pr-8"
        />
        {loading && (
          <Loader2 className="pointer-events-none absolute right-2 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-muted-foreground" />
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

      <ResizableList fill minHeight={120} className="rounded-md border">
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
