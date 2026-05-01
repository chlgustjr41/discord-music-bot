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
