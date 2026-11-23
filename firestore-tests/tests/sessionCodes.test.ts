/**
 * sessionCodes/{code}: `get` allowed, `list` denied, writes denied.
 *
 * Dashboard operations pinned here:
 *   EntryScreen.tsx      getDoc(sessionCodes/{code})
 *   useServerState.ts    onSnapshot(doc sessionCodes/{code})
 */

import { beforeEach, describe, expect, it } from "vitest";
import {
  collection,
  deleteDoc,
  doc,
  getDoc,
  getDocs,
  limit,
  query,
  serverTimestamp,
  setDoc,
  updateDoc,
  where,
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

beforeEach(async () => {
  await setDoc(doc(admin(), "sessionCodes", SESSION_CODE), {
    serverId: GUILD,
    createdAt: serverTimestamp(),
  });
});

describe("sessionCodes — what the dashboard does", () => {
  it("a signed-out visitor can look up a code they were given", async () => {
    const snap = await assertSucceeds(getDoc(doc(anon(), "sessionCodes", SESSION_CODE)));
    expect(snap.data()?.serverId).toBe(GUILD);
  });

  it("the dashboard's real-time listener on one code works", async () => {
    const snap = await assertSucceeds(firstSnapshot(doc(anon(), "sessionCodes", SESSION_CODE)));
    expect(snap.exists()).toBe(true);
  });

  it("a wrong code reads as 'does not exist', not as an error", async () => {
    const snap = await assertSucceeds(getDoc(doc(anon(), "sessionCodes", "ZZZZZZ")));
    expect(snap.exists()).toBe(false);
  });

  it("works the same signed in", async () => {
    await assertSucceeds(getDoc(doc(asUser("alice"), "sessionCodes", SESSION_CODE)));
  });
});

describe("sessionCodes — abuse", () => {
  it.each([
    ["signed out", () => anon()],
    ["signed in", () => asUser("mallory")],
  ])("live codes cannot be enumerated (%s)", async (_who, db) => {
    await assertFails(getDocs(collection(db(), "sessionCodes")));
    await assertFails(getDocs(query(collection(db(), "sessionCodes"), limit(1))));
    await assertFails(firstSnapshot(collection(db(), "sessionCodes")));
  });

  it("a known guild id cannot be turned into its live code with a query", async () => {
    await assertFails(
      getDocs(query(collection(anon(), "sessionCodes"), where("serverId", "==", GUILD))),
    );
  });

  it.each([
    ["signed out", () => anon()],
    ["signed in", () => asUser("mallory")],
  ])("codes cannot be minted, repointed or revoked by a client (%s)", async (_who, db) => {
    await assertFails(
      setDoc(doc(db(), "sessionCodes", "EVIL00"), { serverId: GUILD, createdAt: serverTimestamp() }),
    );
    await assertFails(updateDoc(doc(db(), "sessionCodes", SESSION_CODE), { serverId: "1" }));
    await assertFails(deleteDoc(doc(db(), "sessionCodes", SESSION_CODE)));
  });
});
