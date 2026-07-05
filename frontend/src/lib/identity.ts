/**
 * Session-dashboard identity (FUTURE #3): who is this browser?
 *
 * Priority: Google account displayName > locally saved nickname > "Web User".
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
  viaAccount: boolean;   // true when the name comes from Google auth
  nickname: string;      // the stored nickname (may be empty)
}

function buildSnapshot(): Identity {
  const nickname = (localStorage.getItem(NICKNAME_KEY) ?? "").trim();
  const accountName = authUser?.displayName?.trim() ?? "";
  const name = accountName || nickname || ANONYMOUS_NAME;
  return {
    name,
    named: !!(accountName || nickname),
    viaAccount: !!accountName,
    nickname,
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
