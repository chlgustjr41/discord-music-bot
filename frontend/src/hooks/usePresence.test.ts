/**
 * What this hook actually WRITES, at the only layer that can get it wrong.
 *
 * lib/presence.ts proves which id and which name a browser should use. That is
 * not the same claim as "the hook publishes that": the shape of the document
 * has to match the Firestore rules exactly, and an anonymous row that carries a
 * photoURL, or is written under a uid-shaped id, is rejected by the server and
 * the visitor silently never appears.
 *
 * Firestore is mocked rather than emulated: the assertion is about which calls
 * are made, not what the server does with them. The rules themselves are
 * verified separately, in the emulator.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { User } from "firebase/auth";
import {
  collection,
  deleteDoc,
  doc,
  onSnapshot,
  serverTimestamp,
  setDoc,
} from "firebase/firestore";
import { usePresence } from "./usePresence";
import { colorForUid } from "../lib/presence";

vi.mock("../firebase", () => ({ db: {} }));

// identity.ts reads localStorage and subscribes to Firebase auth at import
// time; the hook only needs the effective name and the per-browser id.
const identity = vi.hoisted(() => ({
  name: "Ada",
  named: true,
  viaAccount: true,
  signedIn: true,
  nickname: "",
  accountName: "Ada",
}));
const BROWSER_ID = "3f7a9c21-4e5b-4c8d-9a1e-77b0c2d3e4f5";

vi.mock("../lib/identity", () => ({
  useIdentity: () => identity,
  getMemberKey: () => "3f7a9c21-4e5b-4c8d-9a1e-77b0c2d3e4f5",
}));

vi.mock("firebase/firestore", () => ({
  collection: vi.fn(() => ({ __type: "collection" })),
  doc: vi.fn((...path: unknown[]) => ({ __type: "doc", path })),
  onSnapshot: vi.fn(() => () => {}),
  setDoc: vi.fn(async () => {}),
  deleteDoc: vi.fn(async () => {}),
  serverTimestamp: vi.fn(() => ({ __type: "serverTimestamp" })),
}));

const signedIn = { uid: "u1", displayName: "Ada", photoURL: null } as User;
const ANON_ID = `anon_${BROWSER_ID}`;

/** The payload of the Nth setDoc call. */
const payload = (n = 0) =>
  vi.mocked(setDoc).mock.calls[n][1] as Record<string, unknown>;

beforeEach(() => {
  vi.clearAllMocks();
  identity.nickname = "";
  identity.name = "Ada";
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("usePresence publishing", () => {
  it("subscribes to nothing without a session code", () => {
    renderHook(() => usePresence(undefined, null));
    expect(onSnapshot).not.toHaveBeenCalled();
  });

  it("writes nothing without a session code, signed in or not", () => {
    renderHook(() => usePresence(undefined, signedIn));
    expect(setDoc).not.toHaveBeenCalled();
  });

  it("publishes when signed in", () => {
    const { result } = renderHook(() => usePresence("ABC123", signedIn));

    expect(result.current.selfId).toBe("u1");
    expect(onSnapshot).toHaveBeenCalled();
    expect(setDoc).toHaveBeenCalled();
    expect(doc).toHaveBeenCalledWith({}, "presence", "ABC123", "participants", "u1");
  });

  it("publishes for a signed-out visitor too, under a namespaced id", () => {
    // Presence is a property of having the page open, not of having an
    // account. The id has to be the anon_ shape or the rules reject the write
    // and the visitor silently never appears.
    const { result } = renderHook(() => usePresence("ABC123", null));

    expect(result.current.selfId).toBe(ANON_ID);
    expect(doc).toHaveBeenCalledWith({}, "presence", "ABC123", "participants", ANON_ID);
    expect(setDoc).toHaveBeenCalled();
    expect(payload().anon).toBe(true);
    // ...and still watches the same collection everyone else does.
    expect(collection).toHaveBeenCalledWith({}, "presence", "ABC123", "participants");
    expect(onSnapshot).toHaveBeenCalled();
  });

  it("never sends a photoURL for an anonymous row", () => {
    // They have none, and the rules forbid the key outright — sending even a
    // null would be sending a field this browser has no business setting.
    renderHook(() => usePresence("ABC123", null));
    expect(payload()).not.toHaveProperty("photoURL");
  });

  it("publishes an empty name for a nameless visitor, not a placeholder", () => {
    // "Anonymous N" is assigned at render, never stored, so two browsers
    // cannot fight over who is "Anonymous 1". Storing "Web User" here would
    // also read as a chosen nickname and suppress the number.
    renderHook(() => usePresence("ABC123", null));
    expect(payload().name).toBe("");
  });

  it("publishes an anonymous visitor's nickname once they set one", () => {
    identity.nickname = "Grace";
    identity.name = "Grace";
    renderHook(() => usePresence("ABC123", null));
    expect(payload().name).toBe("Grace");
  });

  it("colours an anonymous row from its presence id, stable across renders", () => {
    renderHook(() => usePresence("ABC123", null));
    expect(payload().color).toBe(colorForUid(ANON_ID));
  });

  it("keeps the signed-in row free of the anonymous marker", () => {
    renderHook(() => usePresence("ABC123", signedIn));
    expect(payload()).not.toHaveProperty("anon");
    expect(payload()).toHaveProperty("photoURL");
  });

  it("stamps presence with the server clock, never the browser's", () => {
    renderHook(() => usePresence("ABC123", signedIn));

    expect(serverTimestamp).toHaveBeenCalled();
    const [, payload] = vi.mocked(setDoc).mock.calls[0];
    expect((payload as { updatedAt: unknown }).updatedAt).toEqual({
      __type: "serverTimestamp",
    });
  });

  it("publishes the effective identity name, not the raw Google name", () => {
    // Otherwise setting a nickname would need a reload to be seen by anyone.
    renderHook(() =>
      usePresence("ABC123", { ...signedIn, displayName: "Ada Lovelace" } as User),
    );
    expect(payload().name).toBe("Ada");
  });

  it("removes an anonymous row on the way out, like a signed-in one", () => {
    const { unmount } = renderHook(() => usePresence("ABC123", null));
    unmount();
    expect(deleteDoc).toHaveBeenCalled();
  });
});

describe("usePresence snapshot handling", () => {
  /** Drive the snapshot callback the hook registered with onSnapshot. */
  function emit(rows: { id: string; data: object; pending: boolean }[]) {
    const [, next] = vi.mocked(onSnapshot).mock.calls[0] as unknown as [
      unknown,
      (s: unknown) => void,
    ];
    act(() =>
      next({
        docs: rows.map((r) => ({
          id: r.id,
          data: () => r.data,
          metadata: { hasPendingWrites: r.pending },
        })),
      }),
    );
  }

  it("keeps your own row while your write is unacknowledged", () => {
    // The flicker bug, at the layer that actually reads the metadata:
    // dropping `d.metadata.hasPendingWrites` here leaves every pure test
    // green while your avatar still blinks out every 15 seconds.
    const { result } = renderHook(() => usePresence("ABC123", signedIn));
    emit([{ id: "u1", data: { name: "Ada", updatedAt: null }, pending: true }]);
    expect(result.current.participants.map((p) => p.uid)).toEqual(["u1"]);
  });

  it("numbers a nameless anonymous row before handing it to the bar", () => {
    // Resolving the display name has to happen on the way out of the hook;
    // skipping it leaves the bar rendering a blank badge with a "?" initial.
    const { result } = renderHook(() => usePresence("ABC123", null));
    emit([
      { id: "anon_bbbbbbbbbbbb", data: { name: "", updatedAt: Date.now() }, pending: false },
      { id: "anon_aaaaaaaaaaaa", data: { name: "", updatedAt: Date.now() }, pending: false },
      { id: "uid-Z", data: { name: "Ada", updatedAt: Date.now() }, pending: false },
    ]);
    expect(result.current.participants.map((p) => p.name)).toEqual([
      "Anonymous 2",
      "Anonymous 1",
      "Ada",
    ]);
  });

  it("still drops an unresolved row that is nobody's pending write", () => {
    const { result } = renderHook(() => usePresence("ABC123", signedIn));
    emit([{ id: "u2", data: { name: "Bob", updatedAt: null }, pending: false }]);
    expect(result.current.participants).toEqual([]);
  });
});

describe("usePresence focus tracking", () => {
  // jsdom reports hasFocus() === false, so "the window has focus" has to be
  // stated explicitly. That it has to be stated at all is the point of the
  // background-tab test below: the initial value is read, not assumed.
  beforeEach(() => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("publishes focused:false from the FIRST write in a background tab", () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(false);
    renderHook(() => usePresence("ABC123", signedIn));
    const [, payload] = vi.mocked(setDoc).mock.calls[0];
    expect((payload as { focused: boolean }).focused).toBe(false);
  });

  it("starts focused when the tab is visible and the window has focus", () => {
    renderHook(() => usePresence("ABC123", signedIn));
    const [, payload] = vi.mocked(setDoc).mock.calls[0];
    expect((payload as { focused: boolean }).focused).toBe(true);
  });

  it("writes exactly once when the window is blurred", () => {
    renderHook(() => usePresence("ABC123", signedIn));
    const before = vi.mocked(setDoc).mock.calls.length;

    act(() => window.dispatchEvent(new Event("blur")));

    const calls = vi.mocked(setDoc).mock.calls.slice(before);
    expect(calls).toHaveLength(1);
    expect((calls[0][1] as { focused: boolean }).focused).toBe(false);
  });

  it("writes nothing for a second blur with no intervening focus", () => {
    // alt-tab fires blur and visibilitychange in a burst; a write per event
    // is a write storm for a boolean that did not change.
    renderHook(() => usePresence("ABC123", signedIn));
    act(() => window.dispatchEvent(new Event("blur")));
    const after = vi.mocked(setDoc).mock.calls.length;

    act(() => window.dispatchEvent(new Event("blur")));
    act(() => document.dispatchEvent(new Event("visibilitychange")));

    expect(vi.mocked(setDoc).mock.calls).toHaveLength(after);
  });

  it("writes again when focus comes back", () => {
    renderHook(() => usePresence("ABC123", signedIn));
    act(() => window.dispatchEvent(new Event("blur")));
    const after = vi.mocked(setDoc).mock.calls.length;

    act(() => window.dispatchEvent(new Event("focus")));

    const calls = vi.mocked(setDoc).mock.calls.slice(after);
    expect(calls).toHaveLength(1);
    expect((calls[0][1] as { focused: boolean }).focused).toBe(true);
  });

  it("does not delete and re-create the participant document on a focus change", () => {
    // Focus is a field update. If it re-ran the join/leave lifecycle instead,
    // every alt-tab would race a delete against the following write and the
    // avatar could vanish for everyone.
    renderHook(() => usePresence("ABC123", signedIn));
    act(() => window.dispatchEvent(new Event("blur")));
    expect(deleteDoc).not.toHaveBeenCalled();
  });
});
