import { useState } from "react";
import { createPortal } from "react-dom";
import { anchorFor, isAllowedPhotoUrl, type Anchor, type Participant } from "../lib/presence";
import { NicknameDialog } from "./NicknameDialog";

interface Props {
  participants: Participant[];
  /** Which row is you, if you are signed in. Yours is marked and clickable. */
  selfUid?: string | null;
}

const MAX_SHOWN = 4;

/**
 * Who is on this dashboard right now — including you.
 *
 * The ring colour is derived from the uid (see colorForUid), so a person looks
 * the same to everyone and across reloads; it is drawn as an explicit box-shadow
 * rather than a utility class whose colour could drift.
 */
export function PresenceBar({ participants, selfUid = null }: Props) {
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
    <div className="flex items-center -space-x-2">
      {shown.map((p) => {
        const isSelf = !!selfUid && p.uid === selfUid;
        const label = isSelf
          ? `${p.name || "You"} (you) — click to change your name`
          : p.focused
            ? p.name
            : `${p.name} (away)`;

        const inner = (
          <>
            {/* Never issue a request to a host the participant chose: this
                <img> fires for every viewer with no interaction. Falls back to
                the initial, which is what a photo-less account already shows. */}
            {isAllowedPhotoUrl(p.photoURL) ? (
              <img src={p.photoURL!} alt="" className="h-full w-full object-cover" />
            ) : (
              <span className="flex h-full w-full items-center justify-center text-[10px] font-medium text-muted-foreground">
                {(p.name || "?").charAt(0).toUpperCase()}
              </span>
            )}
          </>
        );

        // Greying is two signals on purpose. Opacity alone is easy to miss
        // against a busy header; desaturation alone does nothing to a letter
        // avatar, which has no colour to drain.
        const away = p.focused ? "" : " opacity-40 grayscale";
        const self = isSelf
          ? " cursor-pointer ring-2 ring-primary ring-offset-2 ring-offset-background"
          : "";
        const className =
          "h-6 w-6 shrink-0 overflow-hidden rounded-full bg-muted transition-opacity" +
          away +
          self;

        const common = {
          "data-uid": p.uid,
          "data-focused": String(p.focused),
          "aria-label": label,
          className,
          style: { boxShadow: `0 0 0 2px ${p.color}` },
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
          title={participants.slice(MAX_SHOWN).map((p) => p.name).join(", ")}
          className="flex h-6 shrink-0 items-center rounded-full border bg-muted px-1.5 text-[10px] font-medium text-muted-foreground"
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
            {selfUid && tipped.uid === selfUid && (
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
