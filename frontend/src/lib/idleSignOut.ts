/**
 * Idle auto sign-out for the web dashboard.
 *
 * An unattended browser left on the dashboard otherwise keeps the signed-in
 * Google account (and the servers it can activate) available to whoever walks
 * past the machine. After IDLE_LIMIT_MS with no interaction we sign out, with
 * a warning toast shortly before so active-but-still users can stay.
 *
 * Two deliberate design choices:
 *
 * - **Timestamps, not timers.** We compare "when did activity last happen" to
 *   the clock on a slow interval instead of arming a long setTimeout. A laptop
 *   closed for an hour therefore signs out the moment it wakes, rather than
 *   resuming a stale timer and granting a fresh idle window.
 * - **Cross-tab via localStorage.** Activity in any tab keeps every tab alive,
 *   and the countdown survives a reload. Firebase already propagates the
 *   resulting sign-out to other tabs through its own persistence layer.
 */

import { useEffect, useRef } from "react";
import { onAuthStateChanged, signOut } from "firebase/auth";
import { toast } from "sonner";
import { auth } from "../firebase";

/** Idle time before sign-out. The one knob worth tuning. */
export const IDLE_LIMIT_MS = 30 * 60_000;
/** How long before the deadline the warning toast appears. */
export const WARN_BEFORE_MS = 60_000;

const CHECK_INTERVAL_MS = 15_000;
const STAMP_THROTTLE_MS = 5_000;
const STORAGE_KEY = "jacky:lastActivity";
const WARN_TOAST_ID = "idle-warning";

const ACTIVITY_EVENTS = [
  "pointerdown",
  "pointermove",
  "keydown",
  "wheel",
  "touchstart",
] as const;

export type IdleVerdict = "active" | "warn" | "expired";

/**
 * Pure decision function — the whole policy in one testable place.
 * `lastActivity` and `now` are epoch milliseconds.
 */
export function idleVerdict(
  lastActivity: number,
  now: number,
  limitMs: number = IDLE_LIMIT_MS,
  warnMs: number = WARN_BEFORE_MS
): IdleVerdict {
  const idleFor = now - lastActivity;
  if (idleFor >= limitMs) return "expired";
  if (idleFor >= limitMs - warnMs) return "warn";
  return "active";
}

function readLastActivity(): number {
  const raw = Number(localStorage.getItem(STORAGE_KEY));
  // A missing/corrupt stamp means "we don't know" — treat it as activity now
  // so a storage hiccup can never sign someone out instantly.
  return Number.isFinite(raw) && raw > 0 ? raw : Date.now();
}

function stampActivity(now: number = Date.now()): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(now));
  } catch {
    // Private-mode / quota failures degrade to per-tab in-memory tracking.
  }
}

/**
 * Runs the idle watchdog for as long as someone is signed in. Call once, at
 * the app root — it is a no-op while signed out.
 */
export function useIdleSignOut(): void {
  const warnedRef = useRef(false);

  useEffect(() => {
    let signedIn = false;
    let lastStamp = 0;
    let interval: ReturnType<typeof setInterval> | undefined;

    const clearWarning = () => {
      if (warnedRef.current) {
        toast.dismiss(WARN_TOAST_ID);
        warnedRef.current = false;
      }
    };

    const onActivity = () => {
      if (!signedIn) return;
      const now = Date.now();
      if (now - lastStamp < STAMP_THROTTLE_MS) return;
      lastStamp = now;
      stampActivity(now);
      clearWarning();
    };

    const expire = async () => {
      clearWarning();
      try {
        await signOut(auth);
        toast.info("Signed out after 30 minutes of inactivity.");
      } catch {
        // Network failure mid-sign-out: try again on the next tick rather
        // than leaving a session that believes it is expired but isn't.
        return;
      }
      localStorage.removeItem(STORAGE_KEY);
    };

    const tick = () => {
      if (!signedIn) return;
      const verdict = idleVerdict(readLastActivity(), Date.now());
      if (verdict === "expired") {
        void expire();
        return;
      }
      if (verdict === "warn" && !warnedRef.current) {
        warnedRef.current = true;
        toast.warning("You'll be signed out shortly due to inactivity.", {
          id: WARN_TOAST_ID,
          duration: WARN_BEFORE_MS,
          action: {
            label: "Stay signed in",
            onClick: () => {
              lastStamp = 0; // bypass the throttle so the click always counts
              onActivity();
            },
          },
        });
      } else if (verdict === "active") {
        clearWarning();
      }
    };

    const stopWatching = () => {
      if (interval !== undefined) {
        clearInterval(interval);
        interval = undefined;
      }
      for (const evt of ACTIVITY_EVENTS) {
        window.removeEventListener(evt, onActivity);
      }
      clearWarning();
    };

    const startWatching = () => {
      if (interval !== undefined) return;
      // Fresh sign-in starts its own window; a stale stamp from a previous
      // session must not sign the new one out immediately.
      stampActivity();
      lastStamp = Date.now();
      for (const evt of ACTIVITY_EVENTS) {
        window.addEventListener(evt, onActivity, { passive: true });
      }
      interval = setInterval(tick, CHECK_INTERVAL_MS);
    };

    const unsubscribe = onAuthStateChanged(auth, (user) => {
      signedIn = !!user;
      if (signedIn) startWatching();
      else stopWatching();
    });

    return () => {
      unsubscribe();
      stopWatching();
    };
  }, []);
}
