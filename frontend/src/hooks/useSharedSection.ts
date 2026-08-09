/**
 * A panel's open/closed state, shared with the room when the dashboard is in
 * shared mode and private otherwise.
 *
 * The return shape is deliberately identical to `useState<boolean>`, so a
 * panel adopts it by changing one line:
 *
 *     const [expanded, setExpanded] = useSharedSection("queue", true);
 *
 * When nobody is publishing — solo mode, or signed out — this IS plain local
 * state: the context's setters are no-ops and the remote map is empty, so the
 * hook degrades to exactly the `useState` it replaced.
 */

import { useCallback, useState } from "react";
import { mergeSections } from "../lib/sharedView";
import { useSharedViewContext } from "./useSharedView";

export function useSharedSection(
  id: string,
  defaultOpen: boolean,
): [boolean, (open: boolean) => void] {
  const { sections, setSection, publishing } = useSharedViewContext();

  // Kept even while publishing, so that flipping to solo leaves the panel as
  // the user last saw it rather than snapping back to the default.
  const [local, setLocal] = useState(defaultOpen);

  // mergeSections is what decides that an absent — or malformed — remote value
  // means "use the panel's own default", rather than rendering `undefined`.
  const value = publishing ? mergeSections(sections, { [id]: defaultOpen })[id] : local;

  const set = useCallback(
    (open: boolean) => {
      setLocal(open);
      // A no-op unless publishing. Firestore's own latency compensation
      // delivers the write back through the subscription immediately, so the
      // panel does not wait on a round trip to look like it moved.
      setSection(id, open);
    },
    [id, setSection],
  );

  return [value, set];
}
