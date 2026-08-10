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
