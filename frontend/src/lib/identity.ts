/**
 * Session-dashboard identity (FUTURE #3): who is this browser?
 *
 * Priority: locally saved nickname > Google account displayName > "Web User".
 * The nickname wins deliberately: it is the only name a person can choose
 * here, so an account name overriding it would make the rename UI a no-op for
 * exactly the people who have a nickname set.
 *
 * Components read the CURRENT name at write time via getIdentityName() and
 * stamp it into requestedBy / social stats; React UI subscribes via
 * useSyncExternalStore in useIdentity().
 */

import { useSyncExternalStore } from "react";
import { onAuthStateChanged } from "firebase/auth";
import type { User } from "firebase/auth";
import { auth } from "../firebase";

const NICKNAME_KEY = "jacky:nickname";
const BROWSER_ID_KEY = "jacky:browserId";
export const ANONYMOUS_NAME = "Web User";

let authUser: User | null = null;
let snapshot = buildSnapshot();
const listeners = new Set<() => void>();

export interface Identity {
  name: string;          // effective display name
  named: boolean;        // true when a nickname or account name is set
  viaAccount: boolean;   // true when the SHOWN name comes from Google auth
  signedIn: boolean;     // true whenever there is a Firebase user, nickname or not
  nickname: string;      // the stored nickname (may be empty)
  accountName: string;   // the Google displayName, even when a nickname hides it
}

function buildSnapshot(): Identity {
  const nickname = (localStorage.getItem(NICKNAME_KEY) ?? "").trim();
  const accountName = authUser?.displayName?.trim() ?? "";
  const name = nickname || accountName || ANONYMOUS_NAME;
  return {
    name,
    named: !!(nickname || accountName),
    // Follows the NAME, not the session: with a nickname set the shown name is
    // no longer the account's, so callers that caption where the name came
    // from stay correct. Anything that needs "is there an account?" — the
    // sign-out button, for one — reads signedIn instead.
    viaAccount: !nickname && !!accountName,
    signedIn: !!authUser,
    nickname,
    accountName,
  };
}

function emit() {
  snapshot = buildSnapshot();
  listeners.forEach((l) => l());
}

onAuthStateChanged(auth, (user) => {
  authUser = user;
  emit();
});

export function setNickname(nickname: string) {
  const trimmed = nickname.trim().slice(0, 32);
  if (trimmed) {
    localStorage.setItem(NICKNAME_KEY, trimmed);
  } else {
    localStorage.removeItem(NICKNAME_KEY);
  }
  emit();
}

export function getIdentity(): Identity {
  return snapshot;
}

/**
 * A stable, opaque id for whoever is using this browser — the account uid when
 * signed in, otherwise a random per-browser id.
 *
 * Exists because per-member stats used to be keyed by DISPLAY NAME, so setting
 * a nickname started a fresh leaderboard row instead of renaming the old one,
 * and two people who happened to share a name shared a row. A key that never
 * changes when the name does is the whole point, so this must not be derived
 * from the name in any way.
 *
 * The anonymous id is per-browser, so the same person on a phone and a laptop
 * is two rows — acceptable for someone who has not signed in, and strictly
 * better than the previous behaviour, where they were one row that either of
 * them could rename out from under the other.
 */
export function getMemberKey(): string {
  if (authUser) return authUser.uid;
  let id = localStorage.getItem(BROWSER_ID_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(BROWSER_ID_KEY, id);
  }
  return id;
}

export function getIdentityName(): string {
  return snapshot.name;
}

export function useIdentity(): Identity {
  return useSyncExternalStore(
    (onChange) => {
      listeners.add(onChange);
      return () => listeners.delete(onChange);
    },
    () => snapshot
  );
}
