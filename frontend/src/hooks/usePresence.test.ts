/**
 * The auth gate, at the only layer that can actually leak.
 *
 * lib/presence.ts already proves shouldPublish() returns the right booleans.
 * That is not the same claim as "this hook never writes when it must not":
 * replacing `shouldPublish(mode, !!user)` with `true`, or deleting the guard
 * inside publishCursor, leaves every pure test green while an anonymous
 * visitor broadcasts their cursor to the whole session. Only the Firestore
 * rule would stand between that and production.
 *
 * Firestore is mocked rather than emulated: the assertion is about which calls
 * are made, not what the server does with them.
 */

import { renderHook, act } from "@testing-library/react";
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

vi.mock("firebase/firestore", () => ({
  collection: vi.fn(() => ({ __type: "collection" })),
  doc: vi.fn((...path: unknown[]) => ({ __type: "doc", path })),
  onSnapshot: vi.fn(() => () => {}),
  setDoc: vi.fn(async () => {}),
  deleteDoc: vi.fn(async () => {}),
  serverTimestamp: vi.fn(() => ({ __type: "serverTimestamp" })),
}));

const signedIn = { uid: "u1", displayName: "Ada", photoURL: null } as User;
const rect = { left: 0, top: 0, width: 1000, height: 500 } as DOMRect;

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("usePresence auth gate", () => {
  it("writes nothing at all for an anonymous visitor", () => {
    const { result } = renderHook(() => usePresence("ABC123", null, "shared"));

    act(() => result.current.publishCursor(500, 250, rect));

    expect(setDoc).not.toHaveBeenCalled();
    expect(deleteDoc).not.toHaveBeenCalled();
    expect(result.current.publishing).toBe(false);
  });

  it("does not even subscribe for an anonymous visitor", () => {
    renderHook(() => usePresence("ABC123", null, "shared"));
    expect(onSnapshot).not.toHaveBeenCalled();
    expect(collection).not.toHaveBeenCalled();
  });

  it("writes nothing in solo mode, and removes any doc left behind", () => {
    const { result } = renderHook(() => usePresence("ABC123", signedIn, "solo"));

    act(() => result.current.publishCursor(500, 250, rect));

    expect(setDoc).not.toHaveBeenCalled();
    expect(result.current.publishing).toBe(false);
    // Going solo must not merely stop writing: the doc already published has
    // to go, or the participant stays in everyone's bar until the TTL.
    expect(deleteDoc).toHaveBeenCalled();
  });

  it("does not subscribe in solo mode either — solo hides others too", () => {
    renderHook(() => usePresence("ABC123", signedIn, "solo"));
    expect(onSnapshot).not.toHaveBeenCalled();
  });

  it("publishes when signed in and shared", () => {
    const { result } = renderHook(() => usePresence("ABC123", signedIn, "shared"));

    expect(result.current.publishing).toBe(true);
    expect(onSnapshot).toHaveBeenCalled();
    expect(setDoc).toHaveBeenCalled();
    expect(doc).toHaveBeenCalledWith({}, "presence", "ABC123", "participants", "u1");
  });

  it("stamps presence with the server clock, never the browser's", () => {
    renderHook(() => usePresence("ABC123", signedIn, "shared"));

    expect(serverTimestamp).toHaveBeenCalled();
    const [, payload] = vi.mocked(setDoc).mock.calls[0];
    expect((payload as { updatedAt: unknown }).updatedAt).toEqual({
      __type: "serverTimestamp",
    });
  });

  it("publishes a cursor when signed in and shared", () => {
    const { result } = renderHook(() => usePresence("ABC123", signedIn, "shared"));
    vi.mocked(setDoc).mockClear();

    act(() => result.current.publishCursor(500, 250, rect));

    expect(setDoc).toHaveBeenCalledTimes(1);
    const [, payload] = vi.mocked(setDoc).mock.calls[0];
    expect((payload as { cursor: unknown }).cursor).toEqual({ x: 0.5, y: 0.5 });
  });

  it("ignores a pointer outside the shared area rather than pinning it", () => {
    const { result } = renderHook(() => usePresence("ABC123", signedIn, "shared"));
    vi.mocked(setDoc).mockClear();

    act(() => result.current.publishCursor(5000, 250, rect));

    expect(setDoc).not.toHaveBeenCalled();
  });
});
