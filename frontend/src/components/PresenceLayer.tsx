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
 *
 * ## Why the panels are this component's CHILDREN
 *
 * SharedViewProvider needs the participant roster too, to put a name on the
 * shared search field. Hoisting usePresence back into Dashboard to feed it
 * would undo everything above — so the provider is mounted HERE, where the
 * roster already is, and the panels arrive as a `children` element built by
 * Dashboard.
 *
 * That element's identity is what preserves the isolation: Dashboard does not
 * re-render on a cursor tick, so `children` is the same object on every one of
 * this component's cursor-rate renders, and React skips the whole subtree. The
 * provider in between re-renders, but its context value is memoised on a roster
 * signature that ignores cursors, so no consumer sees a change either.
 *
 * PresenceBar therefore has to reach its place in the header by portal rather
 * than by being rendered there: it is the one piece of presence UI that does
 * not live above the panels. `barSlot` is that place.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { User } from "firebase/auth";
import { usePresence } from "../hooks/usePresence";
import { SharedViewProvider } from "../hooks/useSharedView";
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
  /** Where PresenceBar is portalled — a `display:contents` marker in the
   *  header's control cluster. Null for the first commit, before Dashboard has
   *  a DOM node to hand over. */
  barSlot: HTMLElement | null;
  /** The dashboard panels. See the note above: this must be an element
   *  Dashboard creates, not JSX written inside this component. */
  children: ReactNode;
}

export function PresenceLayer({
  sessionCode,
  user,
  mode,
  containerRef,
  cursorRef,
  barSlot,
  children,
}: Props) {
  const { participants, publishCursor } = usePresence(sessionCode, user, mode);

  // This component is now mounted in both modes — it wraps the panels, and
  // unmounting it on a mode toggle would throw away every panel's state.
  // usePresence already refuses to subscribe or write in solo, so the only
  // thing left to gate is the measuring: a solo dashboard has no cursors to
  // place, and re-measuring on every scroll frame for nobody is exactly the
  // work this file exists to avoid.
  const active = mode === "shared";

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
    if (!active) return;
    measure();
    window.addEventListener("resize", scheduleMeasure);
    window.addEventListener("scroll", scheduleMeasure, { passive: true });
    return () => {
      window.removeEventListener("resize", scheduleMeasure);
      window.removeEventListener("scroll", scheduleMeasure);
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      frame.current = null;
    };
  }, [active, measure, scheduleMeasure]);

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
      {barSlot && createPortal(<PresenceBar participants={participants} />, barSlot)}
      {/* Portalled to the body: the layer is position:fixed, and any ancestor
          with a transform, filter or backdrop-filter would silently become its
          containing block and shift every cursor. */}
      {createPortal(<CursorLayer participants={participants} rect={rect} />, document.body)}
      <SharedViewProvider
        sessionCode={sessionCode}
        user={user}
        mode={mode}
        participants={participants}
      >
        {children}
      </SharedViewProvider>
    </>
  );
}
