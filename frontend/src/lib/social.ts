/**
 * Social-stat writes (FUTURE #3): per-member activity counters and shared
 * queue helpers. All writes are best-effort — they never block the action
 * they describe. Anonymous ("Web User") activity is not counted per-member;
 * setting a nickname or signing in opts the browser into the leaderboard.
 */

import { doc, getDoc, increment, serverTimestamp, setDoc, updateDoc } from "firebase/firestore";
import { db } from "../firebase";
import type { Track } from "../types";
import { ANONYMOUS_NAME, getIdentityName } from "./identity";

export type MemberStatField = "queueAdds" | "drags" | "searches";

export function bumpMemberStat(serverId: string, field: MemberStatField, n = 1): void {
  const name = getIdentityName();
  if (name === ANONYMOUS_NAME) return;
  const key = name.replace(/[/\\.#$[\]]/g, "_").slice(0, 64);
  setDoc(
    doc(db, "servers", serverId, "memberStats", key),
    {
      name,
      [field]: increment(n),
      total: increment(n),
      lastActiveAt: serverTimestamp(),
    },
    { merge: true }
  ).catch(() => {});
}

/** Append tracks to the live queue, stamped with the current identity. */
export async function addTracksToQueue(serverId: string, tracks: Track[]): Promise<void> {
  const stamped = tracks.map((t) => ({ ...t, requestedBy: getIdentityName() }));
  const snap = await getDoc(doc(db, "servers", serverId));
  const queue: Track[] = snap.exists() ? snap.data().queue || [] : [];
  await updateDoc(doc(db, "servers", serverId), { queue: [...queue, ...stamped] });
  bumpMemberStat(serverId, "queueAdds", tracks.length);
}
