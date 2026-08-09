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

/**
 * May a change to a shared text field trigger the side effect a keystroke
 * would normally trigger?
 *
 * SearchPanel debounces a search off every change to `query`. Left alone, a
 * value adopted from the room is indistinguishable from typing, and the result
 * is one bot search per keystroke PER VIEWER: Ada types "radiohead", every
 * other dashboard adopts it, every one of them debounces a search, every one of
 * them writes `searchQuery` to the shared `servers/{id}` document, and the bot
 * answers each duplicate in turn. That is the amplification this function
 * exists to stop, so `adopted` is checked before anything else.
 *
 * The empty-query rule is here too rather than only at the call site, so the
 * whole "should this schedule a search" decision has one tested home.
 */
export function shouldScheduleSearch(args: { adopted: boolean; query: string }): boolean {
  if (args.adopted) return false;
  return args.query.trim().length > 0;
}

/**
 * Takes only the three fields it reads, not a whole Participant: the caller
 * (useSharedView) deliberately hands over a roster stripped of `cursor` and
 * `updatedAt`, because those change ten times a second and would drag a
 * re-render along with them. A full `Participant[]` still satisfies this.
 */
export function typistLabel(
  entry: InputEntry | undefined,
  selfUid: string | null,
  participants: readonly Pick<Participant, "uid" | "name" | "color">[],
): { name: string; color: string } | null {
  if (!entry?.by || entry.by === selfUid) return null;
  const who = participants.find((p) => p.uid === entry.by);
  // A writer who has left should not keep their name on the field.
  return who ? { name: who.name, color: who.color } : null;
}
