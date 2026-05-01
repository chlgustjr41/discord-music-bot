import { useState, useRef, useCallback, type ReactNode } from "react";
import { GripHorizontal } from "lucide-react";

interface Props {
  children: ReactNode;
  defaultHeight?: number;
  minHeight?: number;
  maxHeight?: number;
  className?: string;
}

export function ResizableList({
  children,
  defaultHeight = 288,
  minHeight = 100,
  maxHeight = 800,
  className = "",
}: Props) {
  const [height, setHeight] = useState(defaultHeight);
  const dragging = useRef(false);
  const startY = useRef(0);
  const startH = useRef(0);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      dragging.current = true;
      startY.current = e.clientY;
      startH.current = height;
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
    },
    [height]
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

  return (
    <div className="relative">
      <div
        className={`overflow-y-auto overflow-x-hidden ${className}`}
        style={{ maxHeight: height }}
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
