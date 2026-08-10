import { useState } from "react";
import { createPortal } from "react-dom";
import { anchorFor, isAllowedPhotoUrl, type Anchor, type Participant } from "../lib/presence";
import { NicknameDialog } from "./NicknameDialog";

interface Props {
  participants: Participant[];
  /** Which row is you: a presence document id, uid or `anon_...`, NOT a
   *  uid. Keying on the uid would leave a signed-out visitor unable to click
   *  their own badge to set a nickname. Yours is marked and clickable. */
  selfId?: string | null;
}

const MAX_SHOWN = 6;

/**
 * Who is on this dashboard right now — including you.
 *
 * The ring colour is derived from the id (see colorForUid), so a person looks
 * the same to everyone and across reloads; it is drawn as an explicit
 * box-shadow rather than a utility class whose colour could drift.
 *
 * ## Why nothing here is translucent
 *
 * The avatars overlap when collapsed, and a translucent avatar in a stack
 * shows the one behind it straight through — two half-visible faces sharing
 * the same pixels, which reads as a rendering fault rather than as depth.
 * Everything is opaque, and the two things previously expressed with alpha are
 * expressed some other way:
 *
 * - **Depth** is an outer ring painted in the CARD colour, so each avatar cuts
 *   a clean silhouette out of the one behind it, plus a descending z-index so
 *   the overlap runs in one consistent direction instead of looking shuffled.
 * - **Away** is drained colour — greyscale on the photo and a muted ring
 *   instead of the person's own — not a fade. The ring colour is the identity
 *   signal, so removing it is what "not here" should look like.
 *
 * ## Hover
 *
 * Hovering the group spreads the avatars apart until none overlap, and leaving
 * collapses them back. It is a CSS transition on margin driven by
 * `group-hover`, deliberately not React state: the stack should not have to
 * re-render to animate, and a pointer that leaves the window still resolves.
 */
export function PresenceBar({ participants, selfId = null }: Props) {
  const [tip, setTip] = useState<(Anchor & { uid: string }) | null>(null);
  const [editing, setEditing] = useState(false);

  if (participants.length === 0) return null;

  const shown = participants.slice(0, MAX_SHOWN);
  const overflow = participants.length - shown.length;

  const show = (uid: string) => (e: { currentTarget: HTMLElement }) => {
    const rect = e.currentTarget.getBoundingClientRect();
    setTip({ uid, ...anchorFor(rect, window.innerWidth, window.innerHeight) });
  };
  const hide = () => setTip(null);

  const tipped = tip ? participants.find((p) => p.uid === tip.uid) : undefined;

  return (
    <div className="group flex items-center justify-end">
      {shown.map((p, i) => {
        const isSelf = !!selfId && p.uid === selfId;
        const label = isSelf
          ? `${p.name || "You"} (you) — click to change your name`
          : p.focused
            ? p.name
            : `${p.name} (away)`;

        const inner = isAllowedPhotoUrl(p.photoURL) ? (
          /* Never issue a request to a host the participant chose: this <img>
             fires for every viewer with no interaction. Falls back to the
             initial, which is what a photo-less account already shows. */
          <img
            src={p.photoURL!}
            alt=""
            className={`h-full w-full object-cover${p.focused ? "" : " grayscale"}`}
          />
        ) : (
          <span className="flex h-full w-full items-center justify-center text-[11px] font-semibold text-foreground/70">
            {(p.name || "?").charAt(0).toUpperCase()}
          </span>
        );

        const ring = p.focused ? p.color : "var(--muted-foreground)";
        // Inner ring is identity; outer ring is the card colour and is what
        // separates overlapping avatars. Self adds a third, in the primary
        // colour, rather than a Tailwind `ring-*` that would fight this
        // box-shadow for the same property.
        const boxShadow = isSelf
          ? `0 0 0 2px ${ring}, 0 0 0 4px var(--card), 0 0 0 6px var(--primary)`
          : `0 0 0 2px ${ring}, 0 0 0 4px var(--card)`;

        const common = {
          "data-uid": p.uid,
          "data-focused": String(p.focused),
          "aria-label": label,
          // -ml-2 collapses the stack; group-hover releases it to a real gap.
          // Every avatar including the first carries the margin, so the row
          // grows leftwards and stays pinned to its right-hand edge.
          className:
            "relative h-7 w-7 shrink-0 overflow-hidden rounded-full bg-card" +
            " -ml-2 transition-[margin-left] duration-200 ease-out" +
            " group-hover:ml-1.5 group-focus-within:ml-1.5" +
            (isSelf ? " cursor-pointer" : ""),
          style: {
            boxShadow,
            // Descending: the leftmost sits on top, so the overlap has one
            // direction instead of looking shuffled.
            zIndex: shown.length - i,
          },
          onMouseEnter: show(p.uid),
          onMouseLeave: hide,
          onFocus: show(p.uid),
          onBlur: hide,
        };

        return isSelf ? (
          <button
            key={p.uid}
            type="button"
            data-self="true"
            onClick={() => setEditing(true)}
            {...common}
          >
            {inner}
          </button>
        ) : (
          <span key={p.uid} tabIndex={0} {...common}>
            {inner}
          </span>
        );
      })}

      {overflow > 0 && (
        <span
          data-overflow="true"
          title={participants.slice(MAX_SHOWN).map((p) => p.name).join(", ")}
          className="relative -ml-2 flex h-7 shrink-0 items-center rounded-full bg-muted pl-4 pr-2.5 text-[10px] font-semibold text-muted-foreground transition-[margin-left,padding-left] duration-200 ease-out group-hover:ml-1.5 group-hover:pl-2.5 group-focus-within:ml-1.5 group-focus-within:pl-2.5"
          style={{ boxShadow: "0 0 0 4px var(--card)", zIndex: 0 }}
        >
          +{overflow}
        </span>
      )}

      {/* Portalled out of the header, which is overflow-hidden and would
          otherwise clip this. */}
      {tip &&
        tipped &&
        createPortal(
          <div
            role="tooltip"
            className="pointer-events-none fixed z-[9998] rounded-md border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md"
            style={{
              top: tip.top,
              left: tip.left,
              right: tip.right,
              maxWidth: tip.maxWidth,
              overflowWrap: "anywhere",
            }}
          >
            <span className="font-medium">{tipped.name || "Someone"}</span>
            {selfId && tipped.uid === selfId && (
              <span className="text-muted-foreground"> (you)</span>
            )}
            {!tipped.focused && (
              <span className="block text-muted-foreground">Away — not looking</span>
            )}
          </div>,
          document.body,
        )}

      {editing && <NicknameDialog onClose={() => setEditing(false)} />}
    </div>
  );
}
