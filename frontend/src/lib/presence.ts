/**
 * Pure logic behind dashboard presence.
 *
 * Deliberately free of Firebase imports so every rule here — who publishes,
 * who is still here — can be tested exhaustively without a network or an
 * emulator. The hook that owns the I/O makes no decisions of its own.
 */

export interface Participant {
  /** The presence DOCUMENT id: an account uid, or `anon_<browserId>`. */
  uid: string;
  name: string;
  photoURL: string | null;
  color: string;
  /** Is this person actually looking at the page? See isFocused. */
  focused: boolean;
  updatedAt: number;
}

/**
 * "Looking at this page" means both: a visible tab you have switched away
 * from the window still isn't being read.
 *
 * Pulled out as a pure function because the hook that owns it has to stitch
 * three separate events together (visibilitychange, focus, blur), and the
 * decision those events feed is the only part worth testing exhaustively.
 */
export function isFocused(visibility: string, hasFocus: boolean): boolean {
  return visibility === "visible" && hasFocus;
}

/** Entries older than this are treated as gone. Firestore has no
 *  server-side disconnect hook, so staleness — not onDisconnect — is what
 *  removes someone whose laptop lid closed mid-session. */
export const PRESENCE_TTL_MS = 45_000;
export const HEARTBEAT_MS = 15_000;

/** Stable hue per uid: a person keeps their colour across reloads and looks
 *  the same to everyone, because it is derived rather than assigned. */
export function colorForUid(uid: string): string {
  let hash = 0;
  for (let i = 0; i < uid.length; i++) {
    hash = (hash << 5) - hash + uid.charCodeAt(i);
    hash |= 0;
  }
  return `hsl(${Math.abs(hash) % 360}, 70%, 60%)`;
}

/**
 * Who is still here — including you. Liveness is a fact about a row, not about
 * whose row it is, so the bar (which now shows you alongside everyone else)
 * decides what to do with self rather than having it filtered out down here.
 *
 * `updatedAt` is written with the server's clock (see usePresence), so it is
 * not something a participant can choose — but this stays defensive anyway,
 * because a rules change is one console click away and the failure it would
 * cause is an immortal ghost nobody else can delete.
 *
 * The window is two-sided. Too old is gone; impossibly far in the future is
 * gone too. It is not one-sided (`>= 0`) because `now` is still THIS browser's
 * clock while `updatedAt` is the server's: a viewer running a few seconds slow
 * would otherwise see every live entry as future-dated and see nobody at all.
 * A forged stamp therefore buys at most one extra TTL of afterlife.
 */
export function livingParticipants(all: Participant[], now: number): Participant[] {
  return all.filter((p) => {
    if (typeof p.updatedAt !== "number" || !Number.isFinite(p.updatedAt)) return false;
    const age = now - p.updatedAt;
    return age <= PRESENCE_TTL_MS && age >= -PRESENCE_TTL_MS;
  });
}

/**
 * Firestore hands `updatedAt` back as a Timestamp, and as `null` for a local
 * write the server has not acknowledged yet. Everything above this boundary
 * deals in plain milliseconds, so the conversion happens here, once, and an
 * unresolved field becomes NaN rather than a number that would read as fresh.
 *
 * Duck-typed on `toMillis` rather than imported from firebase/firestore, so
 * this file stays free of Firebase and testable without an emulator.
 */
function toMillis(value: unknown): number {
  if (typeof value === "number") return value;
  if (
    value &&
    typeof value === "object" &&
    typeof (value as { toMillis?: unknown }).toMillis === "function"
  ) {
    return (value as { toMillis: () => number }).toMillis();
  }
  return NaN;
}

/**
 * One presence document, as the UI needs it.
 *
 * `hasPendingWrites` comes straight off the snapshot's metadata and is true
 * exactly when THIS browser has an unacknowledged write on this document. That
 * is the one case where an unresolved `updatedAt` is not a reason to doubt the
 * row: it is our own write, in flight, and the person is demonstrably here.
 * Resolving it to now keeps your avatar on screen for the round trip instead of
 * blinking it out every heartbeat.
 *
 * It is deliberately NOT a general "NaN means fresh" rule — livingParticipants
 * keeps its guard, and an unresolved stamp on anyone else's row still filters
 * it out, because a malformed row must not become immortal.
 */
export function toParticipant(
  uid: string,
  data: Record<string, unknown>,
  hasPendingWrites: boolean,
): Participant {
  const updatedAt = toMillis(data.updatedAt);
  return {
    uid,
    name: typeof data.name === "string" ? data.name : "",
    photoURL: typeof data.photoURL === "string" ? data.photoURL : null,
    color: typeof data.color === "string" ? data.color : "",
    // Defaults to focused. A row written by a client from before this field
    // existed says nothing about attention, and greying someone out on the
    // strength of a missing field would be a lie about them.
    focused: typeof data.focused === "boolean" ? data.focused : true,
    updatedAt:
      hasPendingWrites && !Number.isFinite(updatedAt) ? Date.now() : updatedAt,
  };
}

/**
 * Hosts a participant avatar may be loaded from.
 *
 * `photoURL` is rendered as `<img src>` for everyone on the dashboard, so an
 * arbitrary URL is a zero-interaction beacon: it collects the IP and
 * User-Agent of every viewer, on every render. The rules enforce this too;
 * this is the second layer, because rules can be changed from the console and
 * the component is what actually issues the request.
 *
 * Matched by suffix rather than pinned to lh3: Google account photos have been
 * served from lh3–lh6.googleusercontent.com over the years and the host is not
 * contractual. The suffix check is deliberately written against the parsed
 * hostname, so `evil-googleusercontent.com`, `googleusercontent.com.evil.tld`
 * and `https://lh3.googleusercontent.com@evil.tld/` all fail.
 */
const PHOTO_HOST = "googleusercontent.com";

export function isAllowedPhotoUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.protocol !== "https:") return false;
  const host = parsed.hostname.toLowerCase();
  return host === PHOTO_HOST || host.endsWith(`.${PHOTO_HOST}`);
}

/** The one place that answers "does this browser broadcast?", so the auth
 *  gate cannot be applied in one code path and forgotten in another. */
export function shouldPublish(signedIn: boolean): boolean {
  return signedIn;
}

/** Just enough of a DOMRect to place a tooltip, so this stays testable
 *  without a layout engine. */
export interface AnchorRect {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

/** Where a hover tooltip should be pinned, in viewport coordinates.
 *  Exactly one of `left`/`right` is set — see anchorFor. */
export interface Anchor {
  top: number;
  left?: number;
  right?: number;
  maxWidth: number;
}

/**
 * Anchor a tooltip to an avatar without measuring the tooltip.
 *
 * The dashboard header Card is `overflow-hidden`, so a tooltip positioned
 * inside the row is clipped; it is portalled to document.body with
 * `position: fixed` instead. That moves the problem to keeping it on screen,
 * and measuring the rendered tooltip in order to clamp it would mean setting
 * state from a layout effect — a cascading render, and a lint error here.
 *
 * The geometry avoids the measurement entirely. Cap the width at half the
 * viewport and anchor to whichever edge of the avatar faces the middle: an
 * avatar in the left half grows rightwards from its left edge and so ends at
 * most at (middle + half a viewport); one in the right half grows leftwards
 * from its right edge and so starts at least at (middle - half a viewport).
 * Either way both edges stay on screen, at any width, in one pass.
 *
 * Staying inside the right edge is not cosmetic: a fixed element that pokes
 * past it adds horizontal scroll to the whole dashboard, which at 375px is
 * exactly where the header is already wrapping.
 */
export function anchorFor(rect: AnchorRect, vw: number, vh: number): Anchor {
  const maxWidth = Math.max(120, vw / 2 - 8);
  const below = rect.bottom + 6;
  // Flip above when there is no room below, so this still behaves if the bar
  // is ever moved down the page.
  const top = below + 40 > vh ? Math.max(4, rect.top - 30) : below;
  return rect.left > vw / 2
    ? { top, right: Math.max(4, vw - rect.right), maxWidth }
    : { top, left: Math.max(4, rect.left), maxWidth };
}
