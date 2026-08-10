import { isAllowedPhotoUrl, type Participant } from "../lib/presence";

interface Props {
  participants: Participant[];
}

const MAX_SHOWN = 4;

/**
 * Who is on this dashboard right now. The ring colour is derived from the uid
 * (see colorForUid), so a person looks the same to everyone and across
 * reloads; it is drawn as an explicit 2px box-shadow rather than a utility
 * class whose colour could drift.
 */
export function PresenceBar({ participants }: Props) {
  if (participants.length === 0) return null;

  const shown = participants.slice(0, MAX_SHOWN);
  const overflow = participants.length - shown.length;

  return (
    <div className="flex items-center -space-x-2">
      {shown.map((p) => (
        <div
          key={p.uid}
          title={p.name}
          className="h-6 w-6 shrink-0 overflow-hidden rounded-full bg-muted"
          style={{ boxShadow: `0 0 0 2px ${p.color}` }}
        >
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
        </div>
      ))}

      {overflow > 0 && (
        <span
          title={participants.slice(MAX_SHOWN).map((p) => p.name).join(", ")}
          className="flex h-6 shrink-0 items-center rounded-full border bg-muted px-1.5 text-[10px] font-medium text-muted-foreground"
        >
          +{overflow}
        </span>
      )}
    </div>
  );
}
