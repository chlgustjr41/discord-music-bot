/**
 * Pure rules for the collective dashboard view: which panels are open, what
 * is in the shared text fields, and who last typed there.
 *
 * Free of Firebase imports so the conflict rules — the part that decides
 * whether someone's keystrokes overwrite yours — are testable exhaustively.
 */

import type { Participant } from "./presence";

export const SHARED_INPUT_THROTTLE_MS = 300;
export const MAX_INPUT_LEN = 200;

export interface InputEntry {
  value: string;
  by: string;
}

export function mergeSections(
  remote: Record<string, boolean> | undefined,
  defaults: Record<string, boolean>,
): Record<string, boolean> {
  const out = { ...defaults };
  for (const [id, value] of Object.entries(remote ?? {})) {
    // A malformed remote value must not render the panel `undefined`.
    if (typeof value === "boolean") out[id] = value;
  }
  return out;
}

export function shouldAdoptInput(args: {
  focused: boolean;
  remoteBy: string;
  selfUid: string | null;
  remoteValue: string;
  localValue: string;
}): boolean {
  const { focused, remoteBy, selfUid, remoteValue, localValue } = args;
  // A focused field is never overwritten: you are mid-word.
  if (focused) return false;
  if (!remoteBy || remoteBy === selfUid) return false;
  return remoteValue !== localValue;
}

export function typistLabel(
  entry: InputEntry | undefined,
  selfUid: string | null,
  participants: Participant[],
): { name: string; color: string } | null {
  if (!entry?.by || entry.by === selfUid) return null;
  const who = participants.find((p) => p.uid === entry.by);
  // A writer who has left should not keep their name on the field.
  return who ? { name: who.name, color: who.color } : null;
}
