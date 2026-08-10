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

import { renderHook } from "@testing-library/react";
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

  it("does not even subscribe for an anonymous visitor", () => {
    renderHook(() => usePresence("ABC123", null));
    expect(onSnapshot).not.toHaveBeenCalled();
    expect(collection).not.toHaveBeenCalled();
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
});
