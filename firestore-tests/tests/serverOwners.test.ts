/**
 * serverOwners/{guildId}: holds an email address and a Firebase uid, and is
 * what the bot reads to decide whether a server is activated. No public
 * read; no writes except by the record's own owner.
 *
 * Dashboard operations pinned here:
 *   ActivateServer.tsx   getDoc (is it taken?) then setDoc (activate)
 *   MyServers.tsx        getDocs(where firebaseUid == me)
 */

import { beforeEach, describe, expect, it } from "vitest";
import {
  Timestamp,
  collection,
  deleteDoc,
  doc,
  getDoc,
  getDocs,
  query,
  serverTimestamp,
  setDoc,
  updateDoc,
  where,
  type Firestore,
} from "firebase/firestore";
import {
  GUILD,
  OTHER_GUILD,
  admin,
  anon,
  asUser,
  assertFails,
  assertSucceeds,
} from "./helpers";

const ALICE = "alice";
const BOB = "bob";
const ALICE_GUILD = GUILD;
const ALICE_SECOND_GUILD = "623456789012345678";
const BOB_GUILD = "723456789012345678";
const FREE_GUILD = OTHER_GUILD;

const owner = (db: Firestore, id: string) => doc(db, "serverOwners", id);

/** Exactly what ActivateServer.tsx writes. */
const activation = (uid: string, overrides: Record<string, unknown> = {}) => ({
  ownerDiscordId: "",
  ownerEmail: `${uid}@example.test`,
  firebaseUid: uid,
  activatedAt: serverTimestamp(),
  isActive: true,
  ...overrides,
});

beforeEach(async () => {
  await setDoc(owner(admin(), ALICE_GUILD), activation(ALICE));
  await setDoc(owner(admin(), ALICE_SECOND_GUILD), activation(ALICE, { isActive: false }));
  await setDoc(owner(admin(), BOB_GUILD), activation(BOB));
});

// ── reads ─────────────────────────────────────────────────────────────────

describe("serverOwners — no public read", () => {
  it("a signed-out visitor cannot read any owner record", async () => {
    await assertFails(getDoc(owner(anon(), ALICE_GUILD)));
    await assertFails(getDoc(owner(anon(), FREE_GUILD)));
  });

  it("a signed-out visitor cannot list owner records, constrained or not", async () => {
    const owners = collection(anon(), "serverOwners");
    await assertFails(getDocs(owners));
    await assertFails(getDocs(query(owners, where("firebaseUid", "==", ALICE))));
    await assertFails(getDocs(query(owners, where("isActive", "==", true))));
  });

  it("a signed-in user cannot read someone else's record (email, uid)", async () => {
    await assertFails(getDoc(owner(asUser(BOB), ALICE_GUILD)));
  });

  it("a signed-in user cannot list everyone, or query by someone else's uid", async () => {
    const owners = collection(asUser(BOB), "serverOwners");
    await assertFails(getDocs(owners));
    await assertFails(getDocs(query(owners, where("firebaseUid", "==", ALICE))));
    await assertFails(getDocs(query(owners, where("isActive", "==", true))));
    await assertFails(getDocs(query(owners, where("ownerEmail", "==", "alice@example.test"))));
  });
});

describe("serverOwners — what the dashboard reads (signed in)", () => {
  it("MyServers.tsx: my own records, by a query constrained to my uid", async () => {
    const snap = await assertSucceeds(
      getDocs(query(collection(asUser(ALICE), "serverOwners"), where("firebaseUid", "==", ALICE))),
    );
    expect(snap.docs.map((d) => d.id).sort()).toEqual([ALICE_GUILD, ALICE_SECOND_GUILD].sort());
  });

  it("ActivateServer.tsx: my own record by id", async () => {
    const snap = await assertSucceeds(getDoc(owner(asUser(ALICE), ALICE_GUILD)));
    expect(snap.data()?.ownerEmail).toBe("alice@example.test");
  });

  it("ActivateServer.tsx: a guild with no record yet reads as 'not activated'", async () => {
    const snap = await assertSucceeds(getDoc(owner(asUser(ALICE), FREE_GUILD)));
    expect(snap.exists()).toBe(false);
  });

  it("only snowflake-shaped ids can be probed", async () => {
    await assertFails(getDoc(owner(asUser(ALICE), "not-a-guild")));
  });
});

// ── create ────────────────────────────────────────────────────────────────

describe("serverOwners — activation (create)", () => {
  it("ActivateServer.tsx: a signed-in user activates an unclaimed server as themselves", async () => {
    await assertSucceeds(setDoc(owner(asUser(ALICE), FREE_GUILD), activation(ALICE)));
    const saved = (await getDoc(owner(admin(), FREE_GUILD))).data()!;
    expect(saved).toMatchObject({ firebaseUid: ALICE, isActive: true, ownerEmail: "alice@example.test" });
    expect(saved.activatedAt).toBeInstanceOf(Timestamp);
  });

  it("the email is optional, but if written it must be the caller's own", async () => {
    const { ownerEmail: _dropped, ...withoutEmail } = activation(ALICE);
    await assertSucceeds(setDoc(owner(asUser(ALICE), FREE_GUILD), withoutEmail));
  });

  it("a signed-out visitor cannot activate anything", async () => {
    await assertFails(setDoc(owner(anon(), FREE_GUILD), activation(ALICE)));
  });

  const bad: [string, string, Record<string, unknown>][] = [
    ["naming someone else as the owner", FREE_GUILD, activation(BOB)],
    ["someone else's email address", FREE_GUILD, activation(ALICE, { ownerEmail: "bob@example.test" })],
    ["a made-up email address", FREE_GUILD, activation(ALICE, { ownerEmail: "ceo@example.com" })],
    ["an id that is not a snowflake", "my-cool-server", activation(ALICE)],
    ["an id that is too short", "12345", activation(ALICE)],
    ["an extra field (the legacy localNode blob)", FREE_GUILD, { ...activation(ALICE), localNode: { url: "x", password: "p" } }],
    ["a client-chosen activation time", FREE_GUILD, activation(ALICE, { activatedAt: Timestamp.fromMillis(0) })],
    ["a non-boolean isActive", FREE_GUILD, activation(ALICE, { isActive: "yes" })],
    ["a junk ownerDiscordId", FREE_GUILD, activation(ALICE, { ownerDiscordId: "x".repeat(5000) })],
    ["no firebaseUid", FREE_GUILD, { ownerEmail: "alice@example.test", activatedAt: serverTimestamp(), isActive: true }],
    ["no isActive", FREE_GUILD, { firebaseUid: ALICE, activatedAt: serverTimestamp() }],
  ];

  it.each(bad)("rejects an activation with %s", async (_label, id, data) => {
    await assertFails(setDoc(owner(asUser(ALICE), id), data));
  });
});

// ── takeover / sabotage ───────────────────────────────────────────────────

describe("serverOwners — someone else's record cannot be touched", () => {
  it("overwriting it with yourself as owner (takeover) is denied", async () => {
    await assertFails(setDoc(owner(asUser(BOB), ALICE_GUILD), activation(BOB)));
    await assertFails(setDoc(owner(asUser(BOB), ALICE_GUILD), activation(BOB), { merge: true }));
    await assertFails(updateDoc(owner(asUser(BOB), ALICE_GUILD), { firebaseUid: BOB }));
    expect((await getDoc(owner(admin(), ALICE_GUILD))).data()?.firebaseUid).toBe(ALICE);
  });

  it("deactivating it (so the bot refuses every command there) is denied", async () => {
    await assertFails(updateDoc(owner(asUser(BOB), ALICE_GUILD), { isActive: false }));
    await assertFails(setDoc(owner(asUser(BOB), ALICE_GUILD), { isActive: false }, { merge: true }));
    expect((await getDoc(owner(admin(), ALICE_GUILD))).data()?.isActive).toBe(true);
  });

  it("re-activating a server its owner deactivated is denied", async () => {
    await assertFails(updateDoc(owner(asUser(BOB), ALICE_SECOND_GUILD), { isActive: true }));
  });

  it("deleting it is denied", async () => {
    await assertFails(deleteDoc(owner(asUser(BOB), ALICE_GUILD)));
    expect((await getDoc(owner(admin(), ALICE_GUILD))).exists()).toBe(true);
  });

  it("a signed-out visitor can do none of it either", async () => {
    await assertFails(setDoc(owner(anon(), ALICE_GUILD), activation(BOB)));
    await assertFails(updateDoc(owner(anon(), ALICE_GUILD), { isActive: false }));
    await assertFails(deleteDoc(owner(anon(), ALICE_GUILD)));
  });
});

// ── the owner's own record ────────────────────────────────────────────────

describe("serverOwners — the owner's own record", () => {
  it("ActivateServer.tsx: activating again (full overwrite, fresh timestamp)", async () => {
    await assertSucceeds(setDoc(owner(asUser(ALICE), ALICE_SECOND_GUILD), activation(ALICE)));
    expect((await getDoc(owner(admin(), ALICE_SECOND_GUILD))).data()?.isActive).toBe(true);
  });

  it("the owner can deactivate, leaving activatedAt as it was", async () => {
    await assertSucceeds(updateDoc(owner(asUser(ALICE), ALICE_GUILD), { isActive: false }));
  });

  it("the owner can delete their record", async () => {
    await assertSucceeds(deleteDoc(owner(asUser(ALICE), ALICE_GUILD)));
  });

  it("ownership cannot be handed to another uid, even by the owner", async () => {
    await assertFails(updateDoc(owner(asUser(ALICE), ALICE_GUILD), { firebaseUid: BOB }));
    await assertFails(setDoc(owner(asUser(ALICE), ALICE_GUILD), activation(BOB)));
  });

  it("the owner cannot rewrite the email to someone else's, or add fields", async () => {
    await assertFails(updateDoc(owner(asUser(ALICE), ALICE_GUILD), { ownerEmail: "bob@example.test" }));
    await assertFails(updateDoc(owner(asUser(ALICE), ALICE_GUILD), { localNode: { password: "p" } }));
    await assertFails(
      updateDoc(owner(asUser(ALICE), ALICE_GUILD), { activatedAt: Timestamp.fromMillis(0) }),
    );
  });
});
