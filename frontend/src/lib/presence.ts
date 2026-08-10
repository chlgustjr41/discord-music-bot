/**
 * Pure logic behind dashboard presence.
 *
 * Deliberately free of Firebase imports so every rule here — who publishes,
 * who is still here — can be tested exhaustively without a network or an
 * emulator. The hook that owns the I/O makes no decisions of its own.
 */

export interface Participant {
  uid: string;
  name: string;
  photoURL: string | null;
  color: string;
  updatedAt: number;
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
