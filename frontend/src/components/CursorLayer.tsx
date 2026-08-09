import { type Participant, toPixels } from "../lib/presence";

interface Props {
  participants: Participant[];
  rect: DOMRect | null;
}

/** Roughly how much room the arrow plus a short name pill needs. Used only
 *  to decide which side of the pointer the pill sits on. */
const PILL_W = 120;
const PILL_H = 32;

/**
 * Other people's pointers, drawn over the whole viewport.
 *
 * Cursors arrive at ~10Hz (CURSOR_THROTTLE_MS), which is slow enough that
 * un-eased movement reads as teleporting, hence the short transform
 * transition. Everything is pointer-events:none so this layer can never
 * intercept a click meant for the dashboard.
 */
export function CursorLayer({ participants, rect }: Props) {
  if (!rect) return null;

  const withCursor = participants.filter((p) => p.cursor !== null);
  if (withCursor.length === 0) return null;

  return (
    <div className="fixed inset-0 pointer-events-none z-50">
      {withCursor.map((p) => {
        if (!p.cursor) return null;
        const { x, y } = toPixels(p.cursor, rect);
        // Near the right/bottom edge the pill would be clipped by the
        // viewport, so it flips to the other side of the pointer instead.
        const flipX = x + PILL_W > window.innerWidth;
        const flipY = y + PILL_H > window.innerHeight;

        return (
          <div
            key={p.uid}
            className="absolute left-0 top-0"
            style={{
              transform: `translate3d(${x}px, ${y}px, 0)`,
              transition: "transform 120ms linear",
            }}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 16 16"
              className="absolute"
              style={{
                left: flipX ? -16 : 0,
                top: flipY ? -16 : 0,
                transform: `scale(${flipX ? -1 : 1}, ${flipY ? -1 : 1})`,
              }}
              aria-hidden="true"
            >
              <path d="M1 1 L1 13 L4.5 9.5 L7 15 L9.5 14 L7 8.5 L12 8.5 Z" fill={p.color} />
            </svg>
            <span
              className="absolute whitespace-nowrap rounded px-1.5 py-0.5 text-[10px] font-medium text-white shadow-sm"
              style={{
                backgroundColor: p.color,
                left: flipX ? undefined : 14,
                right: flipX ? 14 : undefined,
                top: flipY ? undefined : 14,
                bottom: flipY ? 14 : undefined,
                maxWidth: PILL_W,
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              {p.name}
            </span>
          </div>
        );
      })}
    </div>
  );
}
