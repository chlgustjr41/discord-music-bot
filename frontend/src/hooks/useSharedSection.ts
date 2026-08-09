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

  // What this panel shows when nobody is publishing. It tracks whatever was
  // last DISPLAYED, not merely what this user clicked: flipping to solo has
  // to leave the panel as the user was last shown it. Written only by the
  // local setter, it would not — Ada opens Stats, Bob (who never touched it)
  // sees it open, flips to solo, and it snaps shut under him.
  const [local, setLocal] = useState(defaultOpen);

  // mergeSections is what decides that an absent — or malformed — remote value
  // means "use the panel's own default", rather than rendering `undefined`.
  const value = publishing ? mergeSections(sections, { [id]: defaultOpen })[id] : local;

  // So the shown value is mirrored back into `local`. During render, not in an
  // effect: this is React's own "adjust state while rendering" pattern, which
  // re-runs this component before anything is committed rather than painting
  // once and correcting afterwards — and setState in an effect is the
  // cascading render this codebase lints against.
  if (publishing && local !== value) setLocal(value);

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
