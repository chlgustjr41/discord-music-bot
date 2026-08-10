/**
 * Social-stat writes (FUTURE #3): per-member activity counters and shared
 * queue helpers. All writes are best-effort — they never block the action
 * they describe. Anonymous ("Web User") activity is not counted per-member;
 * setting a nickname or signing in opts the browser into the leaderboard.
 *
 * Stats are keyed on a STABLE ID (account uid, or a per-browser id), never on
 * the display name. Name-keying meant renaming yourself started a fresh row
 * and left the old one frozen in the leaderboard beside it, and two people
 * with the same display name silently shared a row. The name still travels
 * with the document so the leaderboard can render it — it is data, not
 * identity.
 */

import {
  doc,
  getDoc,
  increment,
  runTransaction,
  serverTimestamp,
  setDoc,
  updateDoc,
} from "firebase/firestore";
import { db } from "../firebase";
import type { Track } from "../types";
import { ANONYMOUS_NAME, getIdentityName, getMemberKey } from "./identity";

export type MemberStatField = "queueAdds" | "drags" | "searches";

const STAT_FIELDS: MemberStatField[] = ["queueAdds", "drags", "searches"];

/** Firestore document-id rules we can violate from a display name: "/" is a
 *  path separator, "." and ".." are traversal, and __x__ is reserved. This is
 *  only still needed to FIND legacy rows — new keys are uids and uuids, which
 *  cannot contain any of it. */
export function legacyMemberKey(name: string): string {
  return name.replace(/[/\\.#$[\]]/g, "_").slice(0, 64);
}

/** Migration is attempted once per server per page load: the transaction is a
 *  no-op when there is nothing to move, but it still costs a read. */
const migrated = new Set<string>();

/**
 * Fold a legacy name-keyed row into the stable-keyed one, once.
 *
 * Without this, everyone who used the leaderboard before the key change would
 * appear twice — an orphaned row frozen at its old totals, and a new one
 * starting from zero.
 *
 * Runs in a transaction because the legacy read and the merged write have to
 * be atomic against a second tab doing the same thing; two plain read-modify
 * -writes would double-count.
 *
 * Only finds a legacy row whose key matches the CURRENT name, so someone who
 * renamed themselves between the old code and this one keeps an orphan. That
 * is accepted: the alternative is scanning the collection on every load.
 */
async function migrateLegacyRow(
  serverId: string,
  key: string,
  name: string,
): Promise<void> {
  const legacyKey = legacyMemberKey(name);
  // A uid that happens to equal the sanitised name would delete the row it
  // just wrote. Vanishingly unlikely, cheap to rule out.
  if (legacyKey === key) return;

  const mark = `${serverId}/${key}`;
  if (migrated.has(mark)) return;
  migrated.add(mark);

  try {
    await runTransaction(db, async (tx) => {
      const legacyRef = doc(db, "servers", serverId, "memberStats", legacyKey);
      const legacySnap = await tx.get(legacyRef);
      if (!legacySnap.exists()) return;

      // Firestore requires every read before any write.
      const currentRef = doc(db, "servers", serverId, "memberStats", key);
      const currentSnap = await tx.get(currentRef);

      const legacy = legacySnap.data();
      const current = currentSnap.exists() ? currentSnap.data() : {};
      const merged: Record<string, unknown> = {
        name,
        lastActiveAt: serverTimestamp(),
      };
      let total = 0;
      for (const field of STAT_FIELDS) {
        const sum = (current[field] ?? 0) + (legacy[field] ?? 0);
        merged[field] = sum;
        total += sum;
      }
      merged.total = total;

      tx.set(currentRef, merged, { merge: true });
      tx.delete(legacyRef);
    });
  } catch {
    // Best-effort, like every write in this file: a failed migration leaves
    // the legacy row in place and costs a duplicate leaderboard entry, which
    // is strictly better than blocking the action that triggered it.
    migrated.delete(mark);
  }
}

export function bumpMemberStat(serverId: string, field: MemberStatField, n = 1): void {
  const name = getIdentityName();
  if (name === ANONYMOUS_NAME) return;
  const key = getMemberKey();

  void migrateLegacyRow(serverId, key, name);

  setDoc(
    doc(db, "servers", serverId, "memberStats", key),
    {
      // Rewritten on every bump, so renaming updates the leaderboard in place
      // instead of forking a row.
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
