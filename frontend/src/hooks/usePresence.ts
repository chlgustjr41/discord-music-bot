/**
 * Publishes this browser's presence, and subscribes to everyone else's, for
 * one session dashboard.
 *
 * Every rule lives in lib/presence.ts; this file is I/O only. It writes
 * nothing at all unless shouldPublish() says so, which is what keeps
 * anonymous visitors invisible.
 */

import { useCallback, useEffect, useState } from "react";
import {
  collection,
  deleteDoc,
  doc,
  onSnapshot,
  serverTimestamp,
  setDoc,
} from "firebase/firestore";
import type { User } from "firebase/auth";
import { db } from "../firebase";
import {
  HEARTBEAT_MS,
  type Participant,
  colorForUid,
  livingParticipants,
  shouldPublish,
} from "../lib/presence";

/** What the last snapshot was for. Stored alongside the rows so a session
 *  or account switch shows nothing rather than the previous room's people
 *  while the new subscription's first snapshot is still in flight. */
interface Snapshot {
  key: string;
  rows: Participant[];
}

const EMPTY: Participant[] = [];

/**
 * Firestore hands `updatedAt` back as a Timestamp, and as `null` for a local
 * write the server has not acknowledged yet. Everything above this boundary —
 * livingParticipants and its tests — deals in plain milliseconds, so the
 * conversion happens here, once, and a pending write becomes NaN rather than
 * a number that would read as fresh.
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

function toParticipant(uid: string, data: Record<string, unknown>): Participant {
  return {
    uid,
    name: typeof data.name === "string" ? data.name : "",
    photoURL: typeof data.photoURL === "string" ? data.photoURL : null,
    color: typeof data.color === "string" ? data.color : "",
    updatedAt: toMillis(data.updatedAt),
  };
}

export function usePresence(sessionCode: string | undefined, user: User | null) {
  const [snapshot, setSnapshot] = useState<Snapshot>({ key: "", rows: EMPTY });
  const [now, setNow] = useState(() => Date.now());

  const publishing = shouldPublish(!!user);
  const selfUid = user?.uid ?? null;
  // Reading presence requires auth (see firestore.rules), so the same gate
  // that stops us broadcasting also stops us subscribing.
  //
  // Empty while auth is still loading (user is null), which is exactly the
  // anonymous case: no subscription, no writes, no UI.
  const key =
    sessionCode && selfUid && publishing ? `${sessionCode}\u0000${selfUid}` : "";

  const selfRef = useCallback(() => {
    if (!sessionCode || !selfUid) return null;
    return doc(db, "presence", sessionCode, "participants", selfUid);
  }, [sessionCode, selfUid]);

  // Subscribe. Reading requires auth (see firestore.rules), so anonymous
  // visitors get nothing and the UI shows nothing.
  //
  // Nothing is cleared here on the way out: setState in an effect body is a
  // cascading render (and a lint error in this codebase). Instead the render
  // below ignores any snapshot whose key is not the current one, which has
  // the same effect without the extra pass.
  useEffect(() => {
    if (!key || !sessionCode) return;
    const unsub = onSnapshot(
      collection(db, "presence", sessionCode, "participants"),
      (snap) => {
        setSnapshot({
          key,
          rows: snap.docs.map((d) => toParticipant(d.id, d.data())),
        });
      },
      // Presence must never take the dashboard down with it.
      () => setSnapshot({ key, rows: EMPTY }),
    );
    return unsub;
  }, [key, sessionCode]);

  // Re-evaluate staleness on a timer: a participant who stops heartbeating
  // produces no snapshot, so nothing would otherwise re-render them away.
  //
  // Only while subscribed. A signed-out dashboard has no presence UI at all,
  // so this was a 5s wake-up (and re-render) for nothing.
  useEffect(() => {
    if (!key) return;
    const id = setInterval(() => setNow(Date.now()), 5_000);
    return () => clearInterval(id);
  }, [key]);

  // Publish + heartbeat, and remove ourselves on the way out.
  //
  // `publishing` is now exactly `!!user`, so the early return above already
  // covers the not-publishing case; it stays in the dependency list because it
  // is still the gate this effect is expressing.
  useEffect(() => {
    const ref = selfRef();
    if (!ref || !user || !publishing) return;
    const write = () =>
      setDoc(
        ref,
        {
          name: user.displayName || "Guest",
          photoURL: user.photoURL ?? null,
          color: colorForUid(user.uid),
          updatedAt: serverTimestamp(),
        },
        { merge: true },
      ).catch(() => {});

    void write();
    const id = setInterval(() => void write(), HEARTBEAT_MS);
    const leave = () => void deleteDoc(ref).catch(() => {});
    window.addEventListener("pagehide", leave);
    return () => {
      clearInterval(id);
      window.removeEventListener("pagehide", leave);
      leave();
    };
  }, [selfRef, publishing, user]);

  const rows = key && snapshot.key === key ? snapshot.rows : EMPTY;

  return {
    participants: livingParticipants(rows, now),
    publishing,
  };
}
