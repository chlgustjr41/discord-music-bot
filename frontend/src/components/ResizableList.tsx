import { useState, useRef, useCallback, type ReactNode } from "react";
import { GripHorizontal } from "lucide-react";

interface Props {
  children: ReactNode;
  /** Initial pixel height. Ignored when `fill` is true (until the user drags the handle). */
  defaultHeight?: number;
  minHeight?: number;
  maxHeight?: number;
  className?: string;
  /**
   * When true, the list fills its flex parent (parent must be a flex column with `min-h-0`).
   * The drag handle still works — the first drag pins the height to a pixel value and
   * the list leaves fill mode for the rest of its lifetime.
   */
  fill?: boolean;
}

export function ResizableList({
  children,
  defaultHeight = 288,
  minHeight = 100,
  maxHeight = 2000,
  className = "",
  fill = false,
}: Props) {
  const [height, setHeight] = useState<number | null>(fill ? null : defaultHeight);
  const dragging = useRef(false);
  const startY = useRef(0);
  const startH = useRef(0);
  const contentRef = useRef<HTMLDivElement>(null);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      dragging.current = true;
      startY.current = e.clientY;
      // If we don't have a fixed height yet (fill mode), seed from the measured content height.
      startH.current = height ?? contentRef.current?.offsetHeight ?? defaultHeight;
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
    },
    [height, defaultHeight]
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!dragging.current) return;
      const delta = e.clientY - startY.current;
      setHeight(Math.min(maxHeight, Math.max(minHeight, startH.current + delta)));
    },
    [minHeight, maxHeight]
  );

  const onPointerUp = useCallback(() => {
    dragging.current = false;
  }, []);

  const inFillMode = height === null;

  return (
    <div className={`relative flex flex-col ${inFillMode ? "flex-1 min-h-0" : ""}`}>
      <div
        ref={contentRef}
        className={`overflow-y-auto overflow-x-hidden ${inFillMode ? "flex-1 min-h-0" : ""} ${className}`}
        style={inFillMode ? undefined : { maxHeight: height ?? undefined }}
      >
        {children}
      </div>
      <div
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        className="flex h-4 cursor-ns-resize items-center justify-center opacity-0 hover:opacity-100 transition-opacity"
      >
        <GripHorizontal className="h-3 w-3 text-muted-foreground" />
      </div>
    </div>
  );
}
