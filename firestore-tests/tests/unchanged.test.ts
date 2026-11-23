/**
 * presence and users/{uid}/pinnedServers were deliberately left as they were.
 * These are regression pins, not a full specification: enough to notice if a
 * later edit to firestore.rules disturbs either block.
 */

import { describe, expect, it } from "vitest";
import {
  collection,
  deleteDoc,
  doc,
  getDoc,
  getDocs,
  serverTimestamp,
  setDoc,
} from "firebase/firestore";
import {
  GUILD,
  SESSION_CODE,
  admin,
  anon,
  asUser,
  assertFails,
  assertSucceeds,
  firstSnapshot,
} from "./helpers";

describe("presence (unchanged)", () => {
  const participants = (db: ReturnType<typeof anon>) =>
    collection(db, "presence", SESSION_CODE, "participants");
  const ANON_ID = "anon_3f2b8c1e-9d4a-4f6b-8a1c-0e5d7b9a2c41";

  const row = (extra: Record<string, unknown> = {}) => ({
    name: "",
    color: "hsl(120, 70%, 60%)",
    focused: true,
    updatedAt: serverTimestamp(),
    ...extra,
  });

  it("usePresence.ts: a signed-out visitor publishes and removes an anon_ row", async () => {
    const ref = doc(participants(anon()), ANON_ID);
    await assertSucceeds(setDoc(ref, row({ anon: true }), { merge: true }));
    await assertSucceeds(deleteDoc(ref));
  });

  it("usePresence.ts: a signed-in user publishes under their own uid, with their Google photo", async () => {
    const ref = doc(participants(asUser("alice")), "alice");
    await assertSucceeds(
      setDoc(
        ref,
        row({ name: "Alice", photoURL: "https://lh3.googleusercontent.com/a/photo=s96-c" }),
        { merge: true },
      ),
    );
  });

  it("usePresence.ts: anyone on the session can see who is here", async () => {
    await setDoc(doc(participants(admin()), "alice"), { name: "Alice" });
    const snap = await assertSucceeds(firstSnapshot(participants(anon())));
    expect(snap.size).toBe(1);
  });

  it("still refuses an anonymous photo, a foreign uid, and an attacker-hosted photo", async () => {
    await assertFails(
      setDoc(doc(participants(anon()), ANON_ID), row({ anon: true, photoURL: "https://evil.example/t.gif" })),
    );
    await assertFails(setDoc(doc(participants(asUser("mallory")), "alice"), row({ name: "Alice" })));
    await assertFails(setDoc(doc(participants(anon()), "alice"), row({ name: "Alice" })));
    await assertFails(
      setDoc(
        doc(participants(asUser("mallory")), "mallory"),
        row({ name: "M", photoURL: "https://evil.example/t.gif" }),
      ),
    );
  });
});

describe("users/{uid}/pinnedServers (unchanged)", () => {
  const pin = (db: ReturnType<typeof anon>, uid: string) =>
    doc(db, "users", uid, "pinnedServers", GUILD);

  it("PinServerButton.tsx / MyServers.tsx: a user manages and lists their own pins", async () => {
    const alice = asUser("alice");
    await assertSucceeds(setDoc(pin(alice, "alice"), { serverName: "Test Guild", pinnedAt: serverTimestamp() }));
    await assertSucceeds(firstSnapshot(pin(alice, "alice")));
    const list = await assertSucceeds(getDocs(collection(alice, "users", "alice", "pinnedServers")));
    expect(list.size).toBe(1);
    await assertSucceeds(deleteDoc(pin(alice, "alice")));
  });

  it("nobody else can read or write them", async () => {
    await setDoc(pin(admin(), "alice"), { serverName: "Test Guild" });
    for (const db of [anon(), asUser("mallory")]) {
      await assertFails(getDoc(pin(db, "alice")));
      await assertFails(getDocs(collection(db, "users", "alice", "pinnedServers")));
      await assertFails(setDoc(pin(db, "alice"), { serverName: "x" }));
      await assertFails(deleteDoc(pin(db, "alice")));
    }
  });
});
