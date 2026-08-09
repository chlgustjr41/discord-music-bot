/**
 * Everything that changes at cursor rate, and nothing else.
 *
 * Remote cursors arrive at roughly CURSOR_THROTTLE_MS per participant, so a
 * busy session produces tens of presence snapshots per second. When
 * usePresence lived in Dashboard, every one of those re-rendered NowPlaying,
 * Queue, SearchPanel, PlaylistManager, MusicHistory, StatsPanel,
 * CommandHistory and ActivityLog — the heaviest screen in the app, several
 * times per second, to move a 16px arrow.
 *
 * Owning the presence state HERE is what fixes that, rather than memoising
 * eight panels one at a time: state that no panel reads cannot re-render a
 * panel, and nobody has to remember to add React.memo to the ninth. The
 * container rect lives here for the same reason — it is remeasured on scroll,
 * which was the second full-tree re-render on the same path.
 *
 * Dashboard keeps only `mode`. It reaches publishCursor through a ref, so
 * pointer movement never crosses a state boundary either.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { User } from "firebase/auth";
import { usePresence } from "../hooks/usePresence";
import type { ViewMode } from "../lib/presence";
import { PresenceBar } from "./PresenceBar";
import { CursorLayer } from "./CursorLayer";

export type PublishCursor = (clientX: number, clientY: number) => void;

interface Props {
  sessionCode: string | undefined;
  user: User | null;
  mode: ViewMode;
  /** The element cursors are normalised against — the dashboard column, so a
   *  4K screen and a laptop agree on where a pointer is. */
  containerRef: React.RefObject<HTMLElement | null>;
  /** Filled in with the pointer sink while this layer is mounted, and cleared
   *  on the way out so a solo dashboard's pointer handler is a no-op. */
  cursorRef: React.RefObject<PublishCursor | null>;
}

export function PresenceLayer({ sessionCode, user, mode, containerRef, cursorRef }: Props) {
  const { participants, publishCursor } = usePresence(sessionCode, user, mode);

  const [rect, setRect] = useState<DOMRect | null>(null);
  // publishCursor reads the ref rather than the state, so it normalises
  // against the freshest measurement even between renders.
  const rectRef = useRef<DOMRect | null>(null);
  const frame = useRef<number | null>(null);

  const measure = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const next = el.getBoundingClientRect();
    rectRef.current = next;
    setRect(next);
  }, [containerRef]);

  // scroll fires far faster than the screen can paint; coalescing to one
  // measurement per frame keeps a flick of the wheel from queueing hundreds of
  // layout reads and renders.
  const scheduleMeasure = useCallback(() => {
    if (frame.current !== null) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      measure();
    });
  }, [measure]);

  useEffect(() => {
    measure();
    window.addEventListener("resize", scheduleMeasure);
    window.addEventListener("scroll", scheduleMeasure, { passive: true });
    return () => {
      window.removeEventListener("resize", scheduleMeasure);
      window.removeEventListener("scroll", scheduleMeasure);
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      frame.current = null;
    };
  }, [measure, scheduleMeasure]);

  useEffect(() => {
    const ref = cursorRef;
    ref.current = (clientX, clientY) => {
      const r = rectRef.current;
      if (r) publishCursor(clientX, clientY, r);
    };
    return () => {
      ref.current = null;
    };
  }, [cursorRef, publishCursor]);

  return (
    <>
      <PresenceBar participants={participants} />
      {/* Portalled to the body: the layer is position:fixed, and any ancestor
          with a transform, filter or backdrop-filter would silently become its
          containing block and shift every cursor. */}
      {createPortal(<CursorLayer participants={participants} rect={rect} />, document.body)}
    </>
  );
}
