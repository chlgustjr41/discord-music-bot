/**
 * Pure logic behind dashboard presence and live cursors.
 *
 * Deliberately free of Firebase imports so every rule here — who publishes,
 * who is still here, where a cursor actually is — can be tested exhaustively
 * without a network or an emulator. The hook that owns the I/O makes no
 * decisions of its own.
 */

export type ViewMode = "shared" | "solo";

export interface Point {
  x: number;
  y: number;
}

export interface Participant {
  uid: string;
  name: string;
  photoURL: string | null;
  color: string;
  cursor: Point | null;
  updatedAt: number;
}

/** Entries older than this are treated as gone. Firestore has no
 *  server-side disconnect hook, so staleness — not onDisconnect — is what
 *  removes someone whose laptop lid closed mid-session. */
export const PRESENCE_TTL_MS = 45_000;
export const HEARTBEAT_MS = 15_000;
export const CURSOR_THROTTLE_MS = 100;
export const CURSOR_MIN_PX = 8;

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
 * Who is still here.
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
export function livingParticipants(
  all: Participant[],
  selfUid: string | null,
  now: number,
): Participant[] {
  return all.filter((p) => {
    if (p.uid === selfUid) return false;
    if (typeof p.updatedAt !== "number" || !Number.isFinite(p.updatedAt)) return false;
    const age = now - p.updatedAt;
    return age <= PRESENCE_TTL_MS && age >= -PRESENCE_TTL_MS;
  });
}

export function toNormalized(
  clientX: number,
  clientY: number,
  rect: DOMRect,
): Point | null {
  if (rect.width <= 0 || rect.height <= 0) return null;
  const x = (clientX - rect.left) / rect.width;
  const y = (clientY - rect.top) / rect.height;
  // Outside the shared area is "not here", not "pinned to the edge".
  if (x < 0 || x > 1 || y < 0 || y > 1) return null;
  return { x, y };
}

export function toPixels(point: Point, rect: DOMRect): Point {
  return { x: rect.left + point.x * rect.width, y: rect.top + point.y * rect.height };
}

export function movedEnough(
  prev: Point | null,
  next: Point,
  minPx: number,
  rect: DOMRect,
): boolean {
  if (!prev) return true;
  const dx = (next.x - prev.x) * rect.width;
  const dy = (next.y - prev.y) * rect.height;
  return Math.hypot(dx, dy) >= minPx;
}

/** The one place that answers "does this browser broadcast?", so the auth
 *  gate cannot be applied in one code path and forgotten in another. */
export function shouldPublish(mode: ViewMode, signedIn: boolean): boolean {
  return signedIn && mode === "shared";
}
