/**
 * Publishes this browser's presence, and subscribes to everyone else's, for
 * one session dashboard.
 *
 * Every rule lives in lib/presence.ts; this file is I/O only. It writes
 * nothing at all unless shouldPublish() says so, which is what keeps
 * anonymous visitors invisible — while still SUBSCRIBING for them, because
 * seeing who is here and appearing in the list are separate permissions.
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
  isFocused,
  livingParticipants,
  shouldPublish,
  toParticipant,
} from "../lib/presence";
import { useIdentity } from "../lib/identity";

/** What the last snapshot was for. Stored alongside the rows so a session
 *  or account switch shows nothing rather than the previous room's people
 *  while the new subscription's first snapshot is still in flight. */
interface Snapshot {
  key: string;
  rows: Participant[];
}

const EMPTY: Participant[] = [];

export function usePresence(sessionCode: string | undefined, user: User | null) {
  const [snapshot, setSnapshot] = useState<Snapshot>({ key: "", rows: EMPTY });
  const [now, setNow] = useState(() => Date.now());

  // Computed, not assumed: a dashboard opened in a background tab must
  // publish focused:false from its very first write rather than claim an
  // attention it does not have until the first event arrives.
  const [focused, setFocused] = useState(() =>
    isFocused(document.visibilityState, document.hasFocus()),
  );
  // The EFFECTIVE name (nickname first), so setting a nickname republishes
  // and everyone else sees it without a reload.
  const identityName = useIdentity().name;

  const publishing = shouldPublish(!!user);
  const selfUid = user?.uid ?? null;
  // Subscribing is gated on the session code ALONE, not on auth: seeing who
  // is here and appearing in the list are separate permissions, and a
  // signed-out visitor gets the first without the second. The uid stays in
  // the key so an account switch shows nothing rather than the previous
  // subscription's rows while the new one's first snapshot is in flight.
  const key = sessionCode ? `${sessionCode}\u0000${selfUid ?? ""}` : "";

  const selfRef = useCallback(() => {
    if (!sessionCode || !selfUid) return null;
    return doc(db, "presence", sessionCode, "participants", selfUid);
  }, [sessionCode, selfUid]);

  // Track "is this person looking at the page?".
  //
  // hasFocus is carried in a closure driven by the focus/blur events rather
  // than re-read from document.hasFocus() on every event, because those two
  // ARE the transitions — and jsdom (plus a couple of real browsers during
  // window-switch animations) reports the pre-transition value if you ask.
  //
  // The write is driven by the VALUE (it is a dependency of the publish
  // effect), never by the event. alt-tab fires visibilitychange and blur
  // together, and a real window switch fires several; React bails out of an
  // update to an Object.is-equal state, so that burst collapses to one state
  // change and therefore one Firestore write instead of a storm for a
  // boolean. The updater form below says so out loud — React would bail out
  // of a plain setFocused(next) identically, so this is documentation of the
  // intent rather than the thing that enforces it.
  useEffect(() => {
    let hasFocus = document.hasFocus();
    const apply = () => {
      const next = isFocused(document.visibilityState, hasFocus);
      setFocused((prev) => (prev === next ? prev : next));
    };
    const onFocus = () => {
      hasFocus = true;
      apply();
    };
    const onBlur = () => {
      hasFocus = false;
      apply();
    };
    document.addEventListener("visibilitychange", apply);
    window.addEventListener("focus", onFocus);
    window.addEventListener("blur", onBlur);
    return () => {
      document.removeEventListener("visibilitychange", apply);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("blur", onBlur);
    };
  }, []);

  // Subscribe whenever there is a session code, signed in or not.
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
          // metadata.hasPendingWrites is per-DOCUMENT and true only for rows
          // this browser has an unacknowledged write on — which is how
          // toParticipant tells "my own write is in flight" (keep it) from
          // "this row has no usable timestamp" (drop it).
          rows: snap.docs.map((d) =>
            toParticipant(d.id, d.data(), d.metadata.hasPendingWrites),
          ),
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
  // Only while subscribed — with no session code there is no presence UI, so
  // this was a 5s wake-up (and re-render) for nothing.
  useEffect(() => {
    if (!key) return;
    const id = setInterval(() => setNow(Date.now()), 5_000);
    return () => clearInterval(id);
  }, [key]);

  // Publish + heartbeat. Re-runs whenever a published FIELD changes (name,
  // focus), which is what makes a nickname or an alt-tab reach everyone else.
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
          name: identityName || "Guest",
          photoURL: user.photoURL ?? null,
          color: colorForUid(user.uid),
          focused,
          updatedAt: serverTimestamp(),
        },
        { merge: true },
      ).catch(() => {});

    void write();
    const id = setInterval(() => void write(), HEARTBEAT_MS);
    return () => clearInterval(id);
  }, [selfRef, publishing, user, identityName, focused]);

  // Leaving is a SEPARATE lifecycle from publishing, and deliberately so.
  //
  // Folded into the effect above, its cleanup would fire on every field
  // change: each alt-tab would delete the participant document and then race
  // a write to re-create it, so everyone else would watch the avatar blink
  // out. This effect only re-runs when the identity of the document itself
  // changes, which is the only time "leave" actually means anything.
  useEffect(() => {
    const ref = selfRef();
    if (!ref || !publishing) return;
    const leave = () => void deleteDoc(ref).catch(() => {});
    window.addEventListener("pagehide", leave);
    return () => {
      window.removeEventListener("pagehide", leave);
      leave();
    };
  }, [selfRef, publishing]);

  const rows = key && snapshot.key === key ? snapshot.rows : EMPTY;

  return {
    participants: livingParticipants(rows, now),
    publishing,
  };
}
