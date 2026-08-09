/**
 * A text field shared with the room, and the chip saying who last typed in it.
 *
 * The caller keeps owning its own `value`/`setValue`; this hook only decides
 * WHEN a remote value should replace the local one, and publishes local edits.
 *
 *     const [query, setQuery] = useState("");
 *     const shared = useSharedInput("search", query, setQuery);
 *     <input value={query} {...} onFocus={shared.onFocus} onBlur={shared.onBlur}
 *            onChange={(e) => { setQuery(e.target.value); shared.publish(e.target.value); }} />
 *
 * `publish` is for LOCAL typing only. Calling it from an adopted change would
 * echo the room's own value back at it forever.
 *
 * ## consumeAdopted, and why it exists
 *
 * A value that arrives from someone else must not be mistaken for the local
 * user typing. SearchPanel fires a search whenever `query` changes; if an
 * adopted value looked like a keystroke, Ada typing would make Bob's browser
 * fire the same search, which writes the query to the shared server document,
 * which the bot then answers — one search per keystroke per viewer, all of
 * them duplicates, and all of them charged to the same quota.
 *
 * So adoption records itself. `consumeAdopted()` returns whether the most
 * recent change came from the room rather than the keyboard, and clears the
 * flag; the caller checks it before running any side effect that a keystroke
 * would normally trigger. It is read-and-clear rather than a plain boolean
 * because it must answer for exactly one change and then stop.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { shouldAdoptInput, typistLabel, type InputEntry } from "../lib/sharedView";
import { useSharedViewContext } from "./useSharedView";

export interface SharedInput {
  onFocus: () => void;
  onBlur: () => void;
  /** Publish a LOCAL edit. Throttled and truncated by the provider. */
  publish: (next: string) => void;
  typist: { name: string; color: string } | null;
  /** True once per adopted remote change; clears itself when read. */
  consumeAdopted: () => boolean;
}

export function useSharedInput(
  id: string,
  value: string,
  setValue: (next: string) => void,
): SharedInput {
  const { inputs, setInput, selfUid, participants } = useSharedViewContext();

  // Focus is tracked here rather than read off the DOM: shouldAdoptInput needs
  // to know whether this user is mid-word at the moment the remote value
  // lands, and document.activeElement is not something a render can depend on.
  const [focused, setFocused] = useState(false);

  // Not state: flipping this must not cause a render, and it is never read
  // during one — only inside the effect that sets it and the callback that
  // clears it.
  const adopted = useRef(false);

  const entry = inputs[id];

  // What the last run of the effect below saw. Adoption is a reaction to the
  // ROOM changing (or to this user stopping typing), never to a local edit:
  // publishing is asynchronous and throttled, so between a local change and
  // its echo the shared document still holds the PREVIOUS value, and an
  // effect that re-decided on every local change would push that stale value
  // straight back over what the user just did. Clearing the field is where
  // that bites — it is the one local edit made with the field unfocused, so
  // nothing else stops the room's older text from reappearing a frame later.
  const lastEntry = useRef<InputEntry | undefined>(undefined);
  const wasFocused = useRef(false);

  useEffect(() => {
    const arrived =
      entry?.value !== lastEntry.current?.value || entry?.by !== lastEntry.current?.by;
    // Blurring is the other trigger: a value that landed while this user was
    // mid-word is deliberately held back until they stop typing.
    const unblurred = wasFocused.current && !focused;
    lastEntry.current = entry;
    wasFocused.current = focused;

    if (!entry) return;
    if (!arrived && !unblurred) return;
    if (
      !shouldAdoptInput({
        focused,
        remoteBy: entry.by,
        selfUid,
        remoteValue: entry.value,
        localValue: value,
      })
    ) {
      return;
    }
    // Order matters: the flag must be set before the change it describes, so
    // that whatever the caller runs in response can already see it.
    adopted.current = true;
    setValue(entry.value);
  }, [entry, focused, selfUid, value, setValue]);

  const onFocus = useCallback(() => setFocused(true), []);
  // Blurring re-runs the effect above, so a value that arrived while this user
  // was typing is picked up the moment they stop.
  const onBlur = useCallback(() => setFocused(false), []);

  const publish = useCallback(
    (next: string) => {
      if (!selfUid) return;
      setInput(id, next, selfUid);
    },
    [id, selfUid, setInput],
  );

  const consumeAdopted = useCallback(() => {
    const was = adopted.current;
    adopted.current = false;
    return was;
  }, []);

  return {
    onFocus,
    onBlur,
    publish,
    typist: typistLabel(entry, selfUid, participants),
    consumeAdopted,
  };
}
