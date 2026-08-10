/**
 * Member-stat keying.
 *
 * The property under test is that the leaderboard key is INDEPENDENT of the
 * display name: renaming yourself must update your row, not fork it, and two
 * people sharing a display name must not share a row.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const authState = vi.hoisted(() => ({ user: null as { uid: string } | null }));

vi.mock("firebase/auth", () => ({
  onAuthStateChanged: (_auth: unknown, cb: (u: unknown) => void) => {
    cb(authState.user);
    return () => {};
  },
}));
vi.mock("../firebase", () => ({ auth: {}, db: {} }));

const fs = vi.hoisted(() => ({
  // Rest params, not `()`: vitest infers a zero-arg signature otherwise and
  // `mock.calls[0][1]` fails tsc even though the test passes.
  doc: vi.fn((...segments: unknown[]) => ({ path: segments.slice(1).join("/") })),
  setDoc: vi.fn(async (...args: unknown[]) => args),
  runTransaction: vi.fn(async (...args: unknown[]) => args),
}));

vi.mock("firebase/firestore", () => ({
  doc: fs.doc,
  setDoc: fs.setDoc,
  runTransaction: fs.runTransaction,
  getDoc: vi.fn(),
  updateDoc: vi.fn(),
  increment: (n: number) => ({ __inc: n }),
  serverTimestamp: () => ({ __ts: true }),
}));

beforeEach(() => {
  localStorage.clear();
  authState.user = null;
  fs.doc.mockClear();
  fs.setDoc.mockClear();
  vi.resetModules();
});

describe("legacyMemberKey", () => {
  it("sanitises the characters Firestore treats as path syntax", async () => {
    const { legacyMemberKey } = await import("./social");
    // "/" is a path separator: an unsanitised name silently addressed a
    // different document, or threw on an odd segment count.
    expect(legacyMemberKey("a/b")).toBe("a_b");
    expect(legacyMemberKey("..")).toBe("__");
    expect(legacyMemberKey("a.b#c$d[e]")).toBe("a_b_c_d_e_");
  });

  it("bounds the length", async () => {
    const { legacyMemberKey } = await import("./social");
    expect(legacyMemberKey("x".repeat(200))).toHaveLength(64);
  });
});

describe("getMemberKey", () => {
  it("is the account uid when signed in", async () => {
    authState.user = { uid: "uid-123" };
    const { getMemberKey } = await import("./identity");
    expect(getMemberKey()).toBe("uid-123");
  });

  it("does not change when the display name changes", async () => {
    // The whole point: a nickname must rename your row, not start a new one.
    authState.user = { uid: "uid-123" };
    const { getMemberKey, setNickname } = await import("./identity");
    const before = getMemberKey();
    setNickname("Something Else");
    expect(getMemberKey()).toBe(before);
  });

  it("gives an anonymous browser a stable id that survives a rename", async () => {
    const { getMemberKey, setNickname } = await import("./identity");
    const first = getMemberKey();
    expect(first).toBeTruthy();
    setNickname("Nick");
    expect(getMemberKey()).toBe(first);
    expect(getMemberKey()).toBe(first);
  });

  it("persists the anonymous id across reloads", async () => {
    const { getMemberKey } = await import("./identity");
    const first = getMemberKey();
    vi.resetModules();
    const reloaded = await import("./identity");
    expect(reloaded.getMemberKey()).toBe(first);
  });

  it("is not derived from the name — two people sharing one do not collide", async () => {
    const { getMemberKey } = await import("./identity");
    const anon = getMemberKey();
    vi.resetModules();
    localStorage.clear();
    authState.user = { uid: "uid-999" };
    const signedIn = await import("./identity");
    expect(signedIn.getMemberKey()).not.toBe(anon);
  });
});

describe("bumpMemberStat", () => {
  it("writes to the stable key and carries the name as data", async () => {
    // The name must travel with the document so the leaderboard can render
    // it — but it must not be what addresses the document.
    authState.user = { uid: "uid-abc" };
    const { setNickname } = await import("./identity");
    setNickname("Renamed");
    const { bumpMemberStat } = await import("./social");

    bumpMemberStat("server-1", "searches");

    const statDoc = fs.doc.mock.calls.find(
      (c) => c[1] === "servers" && c[3] === "memberStats",
    );
    expect(statDoc?.[4]).toBe("uid-abc");
    expect(fs.setDoc.mock.calls[0]?.[1]).toMatchObject({ name: "Renamed" });
  });

  it("counts nothing for an unnamed anonymous browser", async () => {
    const { bumpMemberStat } = await import("./social");
    bumpMemberStat("server-1", "searches");
    expect(fs.setDoc).not.toHaveBeenCalled();
  });
});
