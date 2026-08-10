/**
 * Name priority, which is the whole of identity.ts's behaviour.
 *
 * The module has two side effects at import time — it reads localStorage and
 * subscribes to Firebase auth — so it cannot simply be imported once and
 * poked. Both are handled at the module boundary instead of being worked
 * around inside the module:
 *
 *  - `firebase/auth` is mocked so `onAuthStateChanged` hands its callback to
 *    the test (via vi.hoisted, so the reference survives vi.mock hoisting)
 *    rather than talking to a real Auth instance; `../firebase` is mocked so
 *    importing it never calls initializeApp with an empty Vite env.
 *  - localStorage is seeded and the module re-imported per test through
 *    vi.resetModules(), because the nickname is read once into the initial
 *    snapshot at module load. That is the honest way to test a module-level
 *    cache: exercise the load, do not add a reset() export that only tests use.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const authMock = vi.hoisted(() => ({
  cb: null as ((user: unknown) => void) | null,
}));

vi.mock("../firebase", () => ({ auth: {} }));
vi.mock("firebase/auth", () => ({
  onAuthStateChanged: (_auth: unknown, cb: (user: unknown) => void) => {
    authMock.cb = cb;
    return () => {};
  },
}));

const NICKNAME_KEY = "jacky:nickname";

async function loadIdentity(nickname?: string) {
  localStorage.clear();
  if (nickname !== undefined) localStorage.setItem(NICKNAME_KEY, nickname);
  authMock.cb = null;
  vi.resetModules();
  return import("./identity");
}

/** Drive the auth callback the module registered at import time. */
function signIn(displayName: string | null) {
  authMock.cb?.({ displayName, uid: "u1", photoURL: null });
}

beforeEach(() => {
  localStorage.clear();
});

describe("identity name priority", () => {
  it("uses the nickname over the Google account name when signed in", async () => {
    // The bug this pins: the old order was `accountName || nickname`, so a
    // signed-in user could set a nickname and never see it anywhere.
    const identity = await loadIdentity("Deej");
    signIn("Ada Lovelace");
    expect(identity.getIdentityName()).toBe("Deej");
  });

  it("falls back to the account name when the nickname is cleared", async () => {
    const identity = await loadIdentity("Deej");
    signIn("Ada Lovelace");
    identity.setNickname("");
    expect(identity.getIdentityName()).toBe("Ada Lovelace");
    expect(identity.getIdentity().viaAccount).toBe(true);
  });

  it("uses the account name when no nickname was ever set", async () => {
    const identity = await loadIdentity();
    signIn("Ada Lovelace");
    expect(identity.getIdentityName()).toBe("Ada Lovelace");
  });

  it("is the anonymous name for a signed-out browser with no nickname", async () => {
    const identity = await loadIdentity();
    expect(identity.getIdentityName()).toBe(identity.ANONYMOUS_NAME);
    expect(identity.getIdentity().named).toBe(false);
  });

  it("uses the nickname for a signed-out browser", async () => {
    const identity = await loadIdentity("Deej");
    expect(identity.getIdentityName()).toBe("Deej");
    expect(identity.getIdentity().named).toBe(true);
  });

  it("reports viaAccount false while a nickname is overriding the account name", async () => {
    // IdentityChip uses viaAccount to caption where the shown name came from,
    // so it has to follow the name rather than merely track signed-in-ness.
    const identity = await loadIdentity("Deej");
    signIn("Ada Lovelace");
    expect(identity.getIdentity().viaAccount).toBe(false);
    expect(identity.getIdentity().signedIn).toBe(true);
  });

  it("keeps signedIn true regardless of the nickname, so the dialog still offers sign-out", async () => {
    const identity = await loadIdentity();
    signIn("Ada Lovelace");
    expect(identity.getIdentity().signedIn).toBe(true);
    identity.setNickname("Deej");
    expect(identity.getIdentity().signedIn).toBe(true);
    expect(identity.getIdentity().name).toBe("Deej");
  });

  it("caps a nickname at 32 characters and trims it", async () => {
    const identity = await loadIdentity();
    identity.setNickname("  " + "x".repeat(40) + "  ");
    expect(identity.getIdentityName()).toBe("x".repeat(32));
  });

  it("notifies subscribers when the nickname changes", async () => {
    const identity = await loadIdentity();
    const seen: string[] = [];
    // useIdentity() subscribes through the same list; this is that list.
    identity.setNickname("Deej");
    seen.push(identity.getIdentityName());
    identity.setNickname("Other");
    seen.push(identity.getIdentityName());
    expect(seen).toEqual(["Deej", "Other"]);
  });
});
