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
    <ResizableList defaultHeight={500} minHeight={80} maxHeight={800} className="rounded-md border">
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
