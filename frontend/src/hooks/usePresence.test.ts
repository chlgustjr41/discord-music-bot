/**
 * The auth gate, at the only layer that can actually leak.
 *
 * lib/presence.ts already proves shouldPublish() returns the right boolean.
 * That is not the same claim as "this hook never writes when it must not":
 * replacing `shouldPublish(!!user)` with `true` leaves every pure test green
 * while an anonymous visitor broadcasts their name and photo to the whole
 * session. Only the Firestore rule would stand between that and production.
 *
 * Firestore is mocked rather than emulated: the assertion is about which calls
 * are made, not what the server does with them.
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

vi.mock("../firebase", () => ({ db: {} }));

// identity.ts reads localStorage and subscribes to Firebase auth at import
// time; the hook only needs the effective name out of it.
vi.mock("../lib/identity", () => ({
  useIdentity: () => ({
    name: "Ada",
    named: true,
    viaAccount: true,
    signedIn: true,
    nickname: "",
  }),
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

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("usePresence auth gate", () => {
  it("writes nothing at all for an anonymous visitor", () => {
    const { result } = renderHook(() => usePresence("ABC123", null));

    expect(setDoc).not.toHaveBeenCalled();
    expect(deleteDoc).not.toHaveBeenCalled();
    expect(result.current.publishing).toBe(false);
  });

  it("still subscribes for an anonymous visitor, who is allowed to SEE the bar", () => {
    // Seeing who is here and appearing in the list are separate permissions:
    // reading presence is public (the session code is already the capability),
    // appearing needs a uid to attribute the avatar to.
    renderHook(() => usePresence("ABC123", null));
    expect(onSnapshot).toHaveBeenCalled();
    expect(collection).toHaveBeenCalledWith({}, "presence", "ABC123", "participants");
    expect(setDoc).not.toHaveBeenCalled();
  });

  it("subscribes to nothing without a session code", () => {
    renderHook(() => usePresence(undefined, null));
    expect(onSnapshot).not.toHaveBeenCalled();
  });

  it("publishes when signed in", () => {
    const { result } = renderHook(() => usePresence("ABC123", signedIn));

    expect(result.current.publishing).toBe(true);
    expect(onSnapshot).toHaveBeenCalled();
    expect(setDoc).toHaveBeenCalled();
    expect(doc).toHaveBeenCalledWith({}, "presence", "ABC123", "participants", "u1");
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
    const [, payload] = vi.mocked(setDoc).mock.calls[0];
    expect((payload as { name: string }).name).toBe("Ada");
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
