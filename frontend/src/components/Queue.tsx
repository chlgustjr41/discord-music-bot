import { useState, useEffect, useRef, useCallback } from "react";
import { doc, updateDoc } from "firebase/firestore";
import { db } from "../firebase";
import type { Track } from "../types";
import {
  DndContext,
  closestCenter,
  PointerSensor,
  TouchSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
  DragOverlay,
  type DragStartEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { ResizableList } from "./ResizableList";
import { ListMusic, GripVertical, X, Music, ChevronDown, Trash2, Check, Play } from "lucide-react";

interface Props {
  queue: Track[];
  serverId: string;
}

function trackId(track: Track, index: number): string {
  return `${track.url}::${track.title}::${index}`;
}

function TrackRow({
  track,
  displayIndex,
  onRemove,
  onPlay,
  isDragOverlay,
  isSelectMode,
  isSelected,
  onToggleSelect,
}: {
  track: Track;
  displayIndex: number;
  onRemove?: () => void;
  onPlay?: () => void;
  isDragOverlay?: boolean;
  isSelectMode?: boolean;
  isSelected?: boolean;
  onToggleSelect?: () => void;
}) {
  const mins = Math.floor(track.duration / 60);
  const secs = track.duration % 60;

  return (
    <div
      className={`flex items-center gap-2 rounded-md p-2 transition-colors ${
        isDragOverlay
          ? "bg-card border shadow-lg"
          : isSelected
            ? "bg-destructive/10 border border-destructive/30"
            : "hover:bg-muted/50"
      }`}
    >
      {isSelectMode ? (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onToggleSelect?.();
          }}
          className="shrink-0"
        >
          <div
            className={`flex h-5 w-5 items-center justify-center rounded border ${
              isSelected
                ? "border-destructive bg-destructive text-destructive-foreground"
                : "border-muted-foreground/30"
            }`}
          >
            {isSelected && <Check className="h-3 w-3" />}
          </div>
        </button>
      ) : (
        <div className="cursor-grab touch-none text-muted-foreground hover:text-foreground">
          <GripVertical className="h-4 w-4" />
        </div>
      )}
      <span className="w-6 text-center text-xs text-muted-foreground">{displayIndex}</span>
      {track.thumbnail ? (
        <img src={track.thumbnail} alt="" className="h-10 w-10 rounded object-cover" />
      ) : (
        <div className="flex h-10 w-10 items-center justify-center rounded bg-muted">
          <Music className="h-4 w-4 text-muted-foreground" />
        </div>
      )}
      <div className="flex-1 min-w-0">
        <p className="truncate text-sm font-medium">{track.title}</p>
        <p className="truncate text-xs text-muted-foreground">
          {track.artist} — {mins}:{String(secs).padStart(2, "0")}
        </p>
      </div>
      {!isSelectMode && onPlay && (
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-muted-foreground hover:text-primary"
          onClick={(e) => {
            e.stopPropagation();
            onPlay();
          }}
          title="Play now"
        >
          <Play className="h-3 w-3" />
        </Button>
      )}
      {!isSelectMode && onRemove && (
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-destructive hover:text-destructive"
          onClick={onRemove}
        >
          <X className="h-3 w-3" />
        </Button>
      )}
    </div>
  );
}

function SortableTrack({
  id,
  track,
  displayIndex,
  onRemove,
  onPlay,
  isSelectMode,
  isSelected,
  onToggleSelect,
}: {
  id: string;
  track: Track;
  displayIndex: number;
  onRemove: () => void;
  onPlay: () => void;
  isSelectMode: boolean;
  isSelected: boolean;
  onToggleSelect: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id, disabled: isSelectMode });

  const style = {
    transform: CSS.Translate.toString(transform),
    transition,
    opacity: isDragging ? 0.3 : 1,
  };

  return (
    <li ref={setNodeRef} style={style} {...(isSelectMode ? {} : { ...attributes, ...listeners })}>
      <TrackRow
        track={track}
        displayIndex={displayIndex}
        onRemove={onRemove}
        onPlay={onPlay}
        isSelectMode={isSelectMode}
        isSelected={isSelected}
        onToggleSelect={onToggleSelect}
      />
    </li>
  );
}

export function Queue({ queue, serverId }: Props) {
  const [expanded, setExpanded] = useState(true);
  const [localQueue, setLocalQueue] = useState(queue);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const isDraggingRef = useRef(false);
  const writeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!isDraggingRef.current) {
      setLocalQueue(queue);
    }
  }, [queue]);

  // Exit select mode when queue becomes empty
  useEffect(() => {
    if (localQueue.length === 0) {
      setSelectMode(false);
      setSelected(new Set());
    }
  }, [localQueue.length]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 5 } })
  );

  const ids = localQueue.map((t, i) => trackId(t, i));

  const writeQueue = useCallback(
    (newQueue: Track[]) => {
      if (writeTimer.current) clearTimeout(writeTimer.current);
      writeTimer.current = setTimeout(() => {
        updateDoc(doc(db, "servers", serverId), { queue: newQueue });
      }, 300);
    },
    [serverId]
  );

  const removeTrack = async (index: number) => {
    const updated = [...localQueue];
    updated.splice(index, 1);
    setLocalQueue(updated);
    await updateDoc(doc(db, "servers", serverId), { queue: updated });
  };

  const clearQueue = async () => {
    setLocalQueue([]);
    setSelectMode(false);
    setSelected(new Set());
    await updateDoc(doc(db, "servers", serverId), { queue: [] });
  };

  const playTrack = async (index: number) => {
    const updated = [...localQueue];
    const [track] = updated.splice(index, 1);
    updated.unshift(track);
    setLocalQueue(updated);
    // Write the reordered queue then trigger a skip so the bot plays the top track
    await updateDoc(doc(db, "servers", serverId), {
      queue: updated,
      currentTrack: null,
      isPlaying: true,
    });
  };

  const deleteSelected = async () => {
    const updated = localQueue.filter((_, i) => !selected.has(i));
    setLocalQueue(updated);
    setSelected(new Set());
    setSelectMode(false);
    await updateDoc(doc(db, "servers", serverId), { queue: updated });
  };

  const toggleSelect = (index: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  };

  const selectAllToggle = () => {
    if (selected.size === localQueue.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(localQueue.map((_, i) => i)));
    }
  };

  const handleDragStart = (event: DragStartEvent) => {
    isDraggingRef.current = true;
    setActiveId(String(event.active.id));
  };

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    setActiveId(null);
    isDraggingRef.current = false;

    if (!over || active.id === over.id) return;

    const oldIndex = ids.indexOf(String(active.id));
    const newIndex = ids.indexOf(String(over.id));
    if (oldIndex === -1 || newIndex === -1) return;

    const reordered = arrayMove([...localQueue], oldIndex, newIndex);
    setLocalQueue(reordered);
    writeQueue(reordered);
  };

  const handleDragCancel = () => {
    setActiveId(null);
    isDraggingRef.current = false;
    setLocalQueue(queue);
  };

  const activeIndex = activeId ? ids.indexOf(activeId) : -1;
  const activeTrack = activeIndex >= 0 ? localQueue[activeIndex] : null;

  return (
    <Card>
      <Collapsible open={expanded} onOpenChange={setExpanded}>
        <CardHeader className="pb-3">
          <CollapsibleTrigger className="flex w-full items-center gap-2 cursor-pointer">
            <CardTitle className="flex items-center gap-2 text-lg">
              <ListMusic className="h-4 w-4" />
              Queue
            </CardTitle>
            <Badge variant="secondary" className="ml-auto mr-2">
              {localQueue.length} {localQueue.length === 1 ? "track" : "tracks"}
            </Badge>
            <ChevronDown
              className={`h-4 w-4 text-muted-foreground transition-transform ${expanded ? "rotate-180" : ""}`}
            />
          </CollapsibleTrigger>
        </CardHeader>
        <CollapsibleContent>
          <CardContent className="pt-0 space-y-2">
            {localQueue.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-6 text-muted-foreground">
                <Music className="mb-2 h-8 w-8" />
                <p className="text-sm">Queue is empty</p>
              </div>
            ) : (
              <>
                {/* Queue toolbar */}
                <div className="flex items-center gap-2 flex-wrap">
                  <Button
                    size="sm"
                    variant={selectMode ? "default" : "outline"}
                    onClick={() => {
                      setSelectMode(!selectMode);
                      setSelected(new Set());
                    }}
                  >
                    {selectMode ? "Cancel" : "Select"}
                  </Button>
                  {selectMode && (
                    <>
                      <Button size="sm" variant="outline" onClick={selectAllToggle}>
                        {selected.size === localQueue.length ? "Deselect All" : "Select All"}
                      </Button>
                      {selected.size > 0 && (
                        <>
                          <Badge variant="secondary">{selected.size} selected</Badge>
                          <Button
                            size="sm"
                            variant="destructive"
                            className="ml-auto"
                            onClick={deleteSelected}
                          >
                            <Trash2 className="mr-1 h-3 w-3" />
                            Remove
                          </Button>
                        </>
                      )}
                    </>
                  )}
                  {!selectMode && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="ml-auto text-destructive hover:text-destructive"
                      onClick={clearQueue}
                    >
                      <Trash2 className="mr-1 h-3 w-3" />
                      Clear All
                    </Button>
                  )}
                </div>

                <ResizableList defaultHeight={384} minHeight={100}>
                  <DndContext
                    sensors={sensors}
                    collisionDetection={closestCenter}
                    onDragStart={handleDragStart}
                    onDragEnd={handleDragEnd}
                    onDragCancel={handleDragCancel}
                  >
                    <SortableContext items={ids} strategy={verticalListSortingStrategy}>
                      <ul className="space-y-1">
                        {localQueue.map((track, i) => (
                          <SortableTrack
                            key={ids[i]}
                            id={ids[i]}
                            track={track}
                            displayIndex={i + 1}
                            onRemove={() => removeTrack(i)}
                            onPlay={() => playTrack(i)}
                            isSelectMode={selectMode}
                            isSelected={selected.has(i)}
                            onToggleSelect={() => toggleSelect(i)}
                          />
                        ))}
                      </ul>
                    </SortableContext>
                    <DragOverlay dropAnimation={null}>
                      {activeTrack ? (
                        <TrackRow
                          track={activeTrack}
                          displayIndex={activeIndex + 1}
                          isDragOverlay
                        />
                      ) : null}
                    </DragOverlay>
                  </DndContext>
                </ResizableList>
              </>
            )}
          </CardContent>
        </CollapsibleContent>
      </Collapsible>
    </Card>
  );
}
