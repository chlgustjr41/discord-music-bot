/**
 * Subcollections of servers/{guildId}. Each one is named in the rules; there
 * is no wildcard, so anything not named here must be unreachable.
 *
 *   playlists       bot + dashboard   read, create, update, delete (bounded)
 *   commandHistory  bot writes        dashboard reads + deletes rows
 *   musicHistory    bot writes        dashboard reads + deletes rows
 *   dragStats       dashboard only    read; +1 per write; no delete
 *   memberStats     dashboard only    read; keyed by uid / browser id
 *   history         bot only          nothing for clients
 */

import { beforeEach, describe, expect, it } from "vitest";
import {
  Timestamp,
  addDoc,
  collection,
  collectionGroup,
  deleteDoc,
  doc,
  getDoc,
  getDocs,
  increment,
  limit,
  orderBy,
  query,
  runTransaction,
  serverTimestamp,
  setDoc,
  updateDoc,
  writeBatch,
  type Firestore,
} from "firebase/firestore";
import {
  GUILD,
  admin,
  anon,
  asUser,
  assertFails,
  assertSucceeds,
  firstSnapshot,
  serverDoc,
  track,
} from "./helpers";

const sub = (db: Firestore, name: string, guild = GUILD) =>
  collection(db, "servers", guild, name);

beforeEach(async () => {
  await setDoc(doc(admin(), "servers", GUILD), serverDoc());
});

// ── nothing unnamed ───────────────────────────────────────────────────────

describe("subcollections — only the named ones exist for clients", () => {
  it.each([
    ["signed out", () => anon()],
    ["signed in", () => asUser("mallory")],
  ])("an invented subcollection cannot be created, read or listed (%s)", async (_who, db) => {
    await setDoc(doc(admin(), "servers", GUILD, "secrets", "x"), { a: 1 });
    await assertFails(setDoc(doc(db(), "servers", GUILD, "junk", "x"), { a: 1 }));
    await assertFails(addDoc(sub(db(), "junk"), { blob: "x".repeat(1000) }));
    await assertFails(getDoc(doc(db(), "servers", GUILD, "secrets", "x")));
    await assertFails(getDocs(sub(db(), "secrets")));
  });

  it("nothing can be nested under a named subcollection either", async () => {
    await assertFails(
      setDoc(doc(anon(), "servers", GUILD, "playlists", "p", "extra", "x"), { a: 1 }),
    );
    await assertFails(getDoc(doc(anon(), "servers", GUILD, "playlists", "p", "extra", "x")));
    await assertFails(
      setDoc(doc(anon(), "servers", GUILD, "commandHistory", "c", "notes", "x"), { a: 1 }),
    );
  });

  it("the bot's per-session `history` is not readable or writable by clients", async () => {
    await setDoc(doc(admin(), "servers", GUILD, "history", "20261121-000000"), {
      startedAt: "2026-11-21T00:00:00+00:00",
      endedAt: "2026-11-21T01:00:00+00:00",
      tracks: [track(1)],
    });
    for (const db of [anon(), asUser("alice")]) {
      await assertFails(getDocs(sub(db, "history")));
      await assertFails(getDoc(doc(db, "servers", GUILD, "history", "20261121-000000")));
      await assertFails(setDoc(doc(db, "servers", GUILD, "history", "x"), { tracks: [] }));
      await assertFails(deleteDoc(doc(db, "servers", GUILD, "history", "20261121-000000")));
    }
  });

  it.each(["commandHistory", "musicHistory", "playlists", "memberStats", "dragStats", "history"])(
    "no cross-server sweep: collection-group query on %s is denied",
    async (name) => {
      await assertFails(getDocs(collectionGroup(anon(), name)));
      await assertFails(getDocs(query(collectionGroup(asUser("mallory"), name), limit(1))));
    },
  );

  it("subcollections of a non-snowflake server id are closed", async () => {
    await setDoc(doc(admin(), "servers", "not-a-guild", "playlists", "p"), {
      name: "p",
      tracks: [track(1)],
      createdBy: "x",
      createdAt: serverTimestamp(),
    });
    await assertFails(getDocs(sub(anon(), "playlists", "not-a-guild")));
    await assertFails(getDocs(sub(anon(), "commandHistory", "not-a-guild")));
    await assertFails(
      setDoc(doc(anon(), "servers", "not-a-guild", "playlists", "q"), {
        name: "q",
        tracks: [track(1)],
        createdBy: "Web User",
        createdAt: serverTimestamp(),
      }),
    );
  });
});

// ── playlists ─────────────────────────────────────────────────────────────

describe("playlists", () => {
  const playlist = (name: string, overrides: Record<string, unknown> = {}) => ({
    name,
    tracks: [track(1), track(2)],
    createdBy: "Web User",
    createdAt: serverTimestamp(),
    ...overrides,
  });

  beforeEach(async () => {
    // As j!playlist save writes it.
    await setDoc(doc(admin(), "servers", GUILD, "playlists", "Chill"), {
      name: "Chill",
      tracks: [track(1), track(2), track(3)],
      createdBy: "Bob",
      createdAt: serverTimestamp(),
    });
  });

  it("PlaylistManager.tsx / useUnifiedTrackPool.ts: list all playlists", async () => {
    const snap = await assertSucceeds(getDocs(sub(anon(), "playlists")));
    expect(snap.docs.map((d) => d.id)).toEqual(["Chill"]);
  });

  it("PlaylistEditDialog.tsx: check whether a name is taken", async () => {
    const taken = await assertSucceeds(getDoc(doc(sub(anon(), "playlists"), "Chill")));
    const free = await assertSucceeds(getDoc(doc(sub(anon(), "playlists"), "Road Trip")));
    expect([taken.exists(), free.exists()]).toEqual([true, false]);
  });

  it("PlaylistEditDialog.tsx: create", async () => {
    const batch = writeBatch(anon());
    batch.set(doc(sub(anon(), "playlists"), "Road Trip"), playlist("Road Trip"));
    await assertSucceeds(batch.commit());
  });

  it("PlaylistEditDialog.tsx: edit in place, carrying createdBy / createdAt over", async () => {
    const existing = (await getDoc(doc(sub(anon(), "playlists"), "Chill"))).data()!;
    const batch = writeBatch(anon());
    batch.set(doc(sub(anon(), "playlists"), "Chill"), {
      name: "Chill",
      tracks: [track(3), track(1)],
      createdBy: existing.createdBy,
      createdAt: existing.createdAt,
    });
    await assertSucceeds(batch.commit());
  });

  it("PlaylistEditDialog.tsx: rename (create under the new name + delete the old, one batch)", async () => {
    const existing = (await getDoc(doc(sub(anon(), "playlists"), "Chill"))).data()!;
    const batch = writeBatch(anon());
    batch.set(doc(sub(anon(), "playlists"), "Chill Vol. 2"), {
      name: "Chill Vol. 2",
      tracks: existing.tracks,
      createdBy: existing.createdBy,
      createdAt: existing.createdAt,
    });
    batch.delete(doc(sub(anon(), "playlists"), "Chill"));
    await assertSucceeds(batch.commit());
    const names = (await getDocs(sub(admin(), "playlists"))).docs.map((d) => d.id);
    expect(names).toEqual(["Chill Vol. 2"]);
  });

  it("PlaylistManager.tsx: delete", async () => {
    await assertSucceeds(deleteDoc(doc(sub(anon(), "playlists"), "Chill")));
  });

  it("a 100-character name is fine, counted in characters not bytes", async () => {
    const name = "가".repeat(100);
    await assertSucceeds(setDoc(doc(sub(anon(), "playlists"), name), playlist(name)));
  });

  it("the track-count boundary: 1000 accepted", async () => {
    await assertSucceeds(
      setDoc(
        doc(sub(anon(), "playlists"), "Big"),
        playlist("Big", { tracks: Array.from({ length: 1000 }, (_, i) => ({ t: i })) }),
      ),
    );
  });

  const bad: [string, string, Record<string, unknown>][] = [
    ["name longer than 100 characters", "n".repeat(101), playlist("n".repeat(101))],
    ["name field not matching the document id", "A", playlist("B")],
    ["no tracks", "Empty", playlist("Empty", { tracks: [] })],
    [
      "more than 1000 tracks",
      "Huge",
      playlist("Huge", { tracks: Array.from({ length: 1001 }, (_, i) => ({ t: i })) }),
    ],
    ["tracks not a list", "Odd", playlist("Odd", { tracks: "all of them" })],
    ["createdBy oversized", "Spam", playlist("Spam", { createdBy: "x".repeat(101) })],
    ["createdBy not a string", "Spam", playlist("Spam", { createdBy: 7 })],
    ["createdAt as free text", "When", playlist("When", { createdAt: "yesterday" })],
    ["an extra field", "Extra", { ...playlist("Extra"), blob: "x".repeat(5000) }],
    ["a missing field", "Partial", { name: "Partial", tracks: [track(1)] }],
  ];

  it.each(bad)("rejects a playlist with %s", async (_label, id, data) => {
    await assertFails(setDoc(doc(sub(anon(), "playlists"), id), data));
  });

  it("an existing playlist cannot be turned into a junk drawer by update", async () => {
    await assertFails(updateDoc(doc(sub(anon(), "playlists"), "Chill"), { blob: "x" }));
    await assertFails(updateDoc(doc(sub(anon(), "playlists"), "Chill"), { name: "Other" }));
    await assertFails(updateDoc(doc(sub(anon(), "playlists"), "Chill"), { tracks: [] }));
  });
});

// ── commandHistory / musicHistory ─────────────────────────────────────────

describe("commandHistory", () => {
  let ids: string[] = [];

  beforeEach(async () => {
    ids = [];
    for (const command of ["play", "skip", "volume"]) {
      const ref = await addDoc(sub(admin(), "commandHistory"), {
        command,
        args: command === "play" ? "some song" : "",
        user: "Bob",
        userId: "523456789012345678",
        timestamp: serverTimestamp(),
        callCount: 1,
        source: "discord",
        transcript: "",
      });
      ids.push(ref.id);
    }
  });

  it("CommandHistory.tsx: live query, newest 30", async () => {
    const snap = await assertSucceeds(
      firstSnapshot(query(sub(anon(), "commandHistory"), orderBy("timestamp", "desc"), limit(30))),
    );
    expect(snap.size).toBe(3);
  });

  it("CommandHistory.tsx: Clear All reads the whole collection, then deletes each row", async () => {
    const snap = await assertSucceeds(getDocs(sub(anon(), "commandHistory")));
    for (const d of snap.docs) await assertSucceeds(deleteDoc(d.ref));
    expect((await getDocs(sub(admin(), "commandHistory"))).size).toBe(0);
  });

  it("CommandHistory.tsx: delete selected rows", async () => {
    await assertSucceeds(deleteDoc(doc(sub(anon(), "commandHistory"), ids[0])));
  });

  it.each([
    ["signed out", () => anon()],
    ["signed in", () => asUser("mallory")],
  ])("rows cannot be forged or edited (%s)", async (_who, db) => {
    const forged = {
      command: "play",
      args: "x",
      user: "Admin",
      userId: "1",
      timestamp: serverTimestamp(),
      callCount: 9999,
    };
    await assertFails(addDoc(sub(db(), "commandHistory"), forged));
    await assertFails(setDoc(doc(sub(db(), "commandHistory"), "forged"), forged));
    await assertFails(updateDoc(doc(sub(db(), "commandHistory"), ids[0]), { user: "Someone Else" }));
    await assertFails(updateDoc(doc(sub(db(), "commandHistory"), ids[0]), { callCount: increment(1) }));
  });
});

describe("musicHistory", () => {
  let ids: string[] = [];

  beforeEach(async () => {
    ids = [];
    for (const n of [1, 2, 3]) {
      const ref = await addDoc(sub(admin(), "musicHistory"), {
        ...track(n),
        addedAt: serverTimestamp(),
        playCount: n,
      });
      ids.push(ref.id);
    }
  });

  it("MusicHistory.tsx: live query, newest 50", async () => {
    const snap = await assertSucceeds(
      firstSnapshot(query(sub(anon(), "musicHistory"), orderBy("addedAt", "desc"), limit(50))),
    );
    expect(snap.size).toBe(3);
  });

  it("useUnifiedTrackPool.ts: one-shot, newest 100", async () => {
    await assertSucceeds(
      getDocs(query(sub(anon(), "musicHistory"), orderBy("addedAt", "desc"), limit(100))),
    );
  });

  it("StatsPanel.tsx: live query, most played 100", async () => {
    const snap = await assertSucceeds(
      firstSnapshot(query(sub(anon(), "musicHistory"), orderBy("playCount", "desc"), limit(100))),
    );
    expect(snap.docs[0].data().playCount).toBe(3);
  });

  it("MusicHistory.tsx: delete selected rows", async () => {
    await assertSucceeds(deleteDoc(doc(sub(anon(), "musicHistory"), ids[0])));
  });

  it.each([
    ["signed out", () => anon()],
    ["signed in", () => asUser("mallory")],
  ])("rows cannot be forged or edited (%s)", async (_who, db) => {
    const forged = {
      ...track(9),
      thumbnail: "https://evil.example/t.gif",
      addedAt: serverTimestamp(),
      playCount: 1_000_000,
    };
    await assertFails(addDoc(sub(db(), "musicHistory"), forged));
    await assertFails(setDoc(doc(sub(db(), "musicHistory"), "forged"), forged));
    await assertFails(
      updateDoc(doc(sub(db(), "musicHistory"), ids[0]), { thumbnail: "https://evil.example/t.gif" }),
    );
    await assertFails(updateDoc(doc(sub(db(), "musicHistory"), ids[0]), { playCount: 1_000_000 }));
  });
});

// ── dragStats ─────────────────────────────────────────────────────────────

describe("dragStats", () => {
  // Exactly the key logDrag() derives in Queue.tsx.
  const keyFor = (url: string) => url.replace(/[^A-Za-z0-9_-]/g, "_").slice(-100);
  const t = track(1);
  const KEY = keyFor(t.url as string);

  // A row as an earlier drag left it (only the fields the client writes).
  const existingRow = () => ({
    title: t.title,
    artist: t.artist,
    thumbnail: t.thumbnail,
    url: t.url,
    count: 4,
    lastDraggedAt: serverTimestamp(),
    lastDraggedBy: "Ada",
  });

  const drag = (overrides: Record<string, unknown> = {}) => ({
    title: t.title,
    artist: t.artist,
    thumbnail: t.thumbnail,
    url: t.url,
    count: increment(1),
    lastDraggedAt: serverTimestamp(),
    lastDraggedBy: "Web User",
    ...overrides,
  });

  it("Queue.tsx: first drag creates the row at 1, the next one makes it 2", async () => {
    const ref = doc(sub(anon(), "dragStats"), KEY);
    await assertSucceeds(setDoc(ref, drag(), { merge: true }));
    await assertSucceeds(setDoc(ref, drag({ lastDraggedBy: "Ada" }), { merge: true }));
    expect((await getDoc(doc(sub(admin(), "dragStats"), KEY))).data()?.count).toBe(2);
  });

  it("Queue.tsx: a track with no artist or thumbnail (empty strings)", async () => {
    await assertSucceeds(
      setDoc(doc(sub(anon(), "dragStats"), KEY), drag({ artist: "", thumbnail: "" }), { merge: true }),
    );
  });

  it("StatsPanel.tsx: live query, most dragged 10", async () => {
    await setDoc(doc(sub(admin(), "dragStats"), KEY), existingRow());
    const snap = await assertSucceeds(
      firstSnapshot(query(sub(anon(), "dragStats"), orderBy("count", "desc"), limit(10))),
    );
    expect(snap.size).toBe(1);
  });

  const bad: [string, string, Record<string, unknown>][] = [
    ["a planted score on create", KEY, drag({ count: 9999 })],
    ["a count that is not an integer", KEY, drag({ count: "many" })],
    ["a client-chosen timestamp", KEY, drag({ lastDraggedAt: Timestamp.fromMillis(4102444800000) })],
    ["an oversized title", KEY, drag({ title: "t".repeat(301) })],
    ["an oversized artist", KEY, drag({ artist: "a".repeat(301) })],
    ["an oversized thumbnail url", KEY, drag({ thumbnail: "https://x.example/" + "a".repeat(2048) })],
    ["an oversized url", KEY, drag({ url: "https://x.example/" + "a".repeat(2048) })],
    ["an oversized name", KEY, drag({ lastDraggedBy: "n".repeat(129) })],
    ["a non-string title", KEY, drag({ title: 1 })],
    ["an extra field", KEY, { ...drag(), blob: "x".repeat(5000) }],
    ["no url", KEY, { title: "x", count: increment(1), lastDraggedAt: serverTimestamp() }],
    ["an id with characters the client never produces", "has.dot", drag()],
    ["an id longer than the client ever produces", "k".repeat(101), drag()],
  ];

  it.each(bad)("rejects %s", async (_label, key, data) => {
    await assertFails(setDoc(doc(sub(anon(), "dragStats"), key), data, { merge: true }));
  });

  it("the score moves one step at a time — no jumps, no resets", async () => {
    await setDoc(doc(sub(admin(), "dragStats"), KEY), existingRow());
    const ref = doc(sub(anon(), "dragStats"), KEY);
    await assertFails(setDoc(ref, drag({ count: 1000 }), { merge: true }));
    await assertFails(setDoc(ref, drag({ count: increment(50) }), { merge: true }));
    await assertFails(setDoc(ref, drag({ count: 0 }), { merge: true }));
    await assertFails(setDoc(ref, drag({ count: increment(-1) }), { merge: true }));
    await assertSucceeds(setDoc(ref, drag(), { merge: true }));
  });

  it("rows cannot be deleted by a client", async () => {
    await setDoc(doc(sub(admin(), "dragStats"), KEY), existingRow());
    await assertFails(deleteDoc(doc(sub(anon(), "dragStats"), KEY)));
    await assertFails(deleteDoc(doc(sub(asUser("mallory"), "dragStats"), KEY)));
  });
});

// ── memberStats ───────────────────────────────────────────────────────────

describe("memberStats", () => {
  const BROWSER = "3f2b8c1e-9d4a-4f6b-8a1c-0e5d7b9a2c41"; // a crypto.randomUUID()
  const ALICE_UID = "AliceUidAliceUidAliceUid0001"; // 28 chars, like a Firebase uid
  const BOB_UID = "BobUidBobUidBobUidBobUid0002";

  // Exactly what bumpMemberStat() writes.
  const bump = (name: string, field: string, n = 1, extra: Record<string, unknown> = {}) => ({
    name,
    [field]: increment(n),
    total: increment(n),
    lastActiveAt: serverTimestamp(),
    ...extra,
  });

  it("social.ts: a signed-out browser with a nickname counts under its browser id", async () => {
    const ref = doc(sub(anon(), "memberStats"), BROWSER);
    await assertSucceeds(setDoc(ref, bump("Ada", "searches"), { merge: true }));
    await assertSucceeds(setDoc(ref, bump("Ada", "queueAdds", 25), { merge: true }));
    await assertSucceeds(setDoc(ref, bump("Ada L.", "drags"), { merge: true }));
    expect((await getDoc(doc(sub(admin(), "memberStats"), BROWSER))).data()).toMatchObject({
      name: "Ada L.",
      searches: 1,
      queueAdds: 25,
      drags: 1,
      total: 27,
    });
  });

  it("social.ts: a signed-in account counts under its uid", async () => {
    const ref = doc(sub(asUser(ALICE_UID), "memberStats"), ALICE_UID);
    await assertSucceeds(setDoc(ref, bump("Alice", "queueAdds", 3), { merge: true }));
    await assertSucceeds(setDoc(ref, bump("Alice", "drags"), { merge: true }));
  });

  it("StatsPanel.tsx: live query, top 10 members", async () => {
    await setDoc(doc(sub(admin(), "memberStats"), BROWSER), { name: "Ada", total: 5 });
    const snap = await assertSucceeds(
      firstSnapshot(query(sub(anon(), "memberStats"), orderBy("total", "desc"), limit(10))),
    );
    expect(snap.size).toBe(1);
  });

  it("social.ts migrateLegacyRow: fold a legacy name-keyed row into the stable one, atomically", async () => {
    // The legacy id is the sanitised display name — free text, spaces and all.
    const LEGACY = "Ada Lovelace";
    await setDoc(doc(sub(admin(), "memberStats"), LEGACY), {
      name: LEGACY,
      queueAdds: 4,
      drags: 2,
      total: 6,
    });
    const db = anon();
    await assertSucceeds(
      runTransaction(db, async (tx) => {
        const legacyRef = doc(sub(db, "memberStats"), LEGACY);
        const legacy = await tx.get(legacyRef);
        const currentRef = doc(sub(db, "memberStats"), BROWSER);
        const current = await tx.get(currentRef);
        expect([legacy.exists(), current.exists()]).toEqual([true, false]);
        tx.set(
          currentRef,
          {
            name: LEGACY,
            lastActiveAt: serverTimestamp(),
            queueAdds: 4,
            drags: 2,
            searches: 0,
            total: 6,
          },
          { merge: true },
        );
        tx.delete(legacyRef);
      }),
    );
    const rows = (await getDocs(sub(admin(), "memberStats"))).docs.map((d) => d.id);
    expect(rows).toEqual([BROWSER]);
  });

  it("a signed-out visitor cannot write an account's row", async () => {
    await setDoc(doc(sub(admin(), "memberStats"), ALICE_UID), { name: "Alice", total: 40, queueAdds: 40 });
    const ref = doc(sub(anon(), "memberStats"), ALICE_UID);
    await assertFails(setDoc(ref, bump("Alice is a fraud", "drags"), { merge: true }));
    await assertFails(deleteDoc(ref));
  });

  it("one account cannot write or delete another account's row", async () => {
    await setDoc(doc(sub(admin(), "memberStats"), ALICE_UID), { name: "Alice", total: 40, queueAdds: 40 });
    const ref = doc(sub(asUser(BOB_UID), "memberStats"), ALICE_UID);
    await assertFails(setDoc(ref, bump("Bob was here", "drags"), { merge: true }));
    await assertFails(deleteDoc(ref));
  });

  it("current-format rows are never deletable — only the legacy migration deletes anything", async () => {
    // No dashboard screen deletes a uid-keyed or browser-keyed row, so the
    // rules do not let a client do it: not a stranger, not the row's owner.
    await setDoc(doc(sub(admin(), "memberStats"), ALICE_UID), { name: "Alice", total: 40, queueAdds: 40 });
    await setDoc(doc(sub(admin(), "memberStats"), BROWSER), { name: "Ada", total: 5, drags: 5 });
    await assertFails(deleteDoc(doc(sub(asUser(ALICE_UID), "memberStats"), ALICE_UID)));
    for (const db of [anon(), asUser(BOB_UID)]) {
      await assertFails(deleteDoc(doc(sub(db, "memberStats"), BROWSER)));
      await assertFails(deleteDoc(doc(sub(db, "memberStats"), ALICE_UID)));
    }
    expect((await getDocs(sub(admin(), "memberStats"))).size).toBe(2);
  });

  it("rows cannot be created under arbitrary ids", async () => {
    await assertFails(
      setDoc(doc(sub(anon(), "memberStats"), "Totally A Person"), bump("x", "drags"), { merge: true }),
    );
    await assertFails(
      setDoc(doc(sub(asUser(BOB_UID), "memberStats"), "leaderboard-spam-1"), bump("x", "drags"), {
        merge: true,
      }),
    );
  });

  const bad: [string, Record<string, unknown>][] = [
    ["an oversized name", bump("n".repeat(129), "drags")],
    ["an empty name", bump("", "drags")],
    ["a non-string name", bump("x", "drags", 1, { name: 7 })],
    ["a client-chosen timestamp", bump("x", "drags", 1, { lastActiveAt: Timestamp.fromMillis(0) })],
    ["a negative counter", { name: "x", drags: -5, total: 0, lastActiveAt: serverTimestamp() }],
    ["a fractional counter", { name: "x", drags: 0.5, total: 1, lastActiveAt: serverTimestamp() }],
    ["a text counter", { name: "x", searches: "lots", total: 1, lastActiveAt: serverTimestamp() }],
    ["an absurd total", { name: "x", total: 1_000_000_001, lastActiveAt: serverTimestamp() }],
    ["an extra field", bump("x", "drags", 1, { blob: "x".repeat(5000) })],
    ["a counter field the dashboard does not have", bump("x", "wins")],
    ["no name", { drags: increment(1), total: increment(1), lastActiveAt: serverTimestamp() }],
  ];

  it.each(bad)("rejects %s", async (_label, data) => {
    await assertFails(setDoc(doc(sub(anon(), "memberStats"), BROWSER), data, { merge: true }));
  });
});
