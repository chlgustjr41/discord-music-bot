/**
 * The one subscription behind the collective dashboard view.
 *
 * `presence/{sessionCode}/shared/view` holds `{ sections, inputs, updatedAt }`
 * for the whole room: which panels are open, and what is in the shared text
 * fields. Seven panels and a search box consume it, so it is read ONCE here
 * and handed down through context — seven listeners on one document would be
 * seven times the reads for exactly the same bytes.
 *
 * Every rule lives in lib/sharedView.ts; this file is I/O only, in the same
 * split as usePresence/lib/presence. The auth gate is not re-derived either:
 * shouldPublish() is imported, so a solo or anonymous dashboard neither reads
 * nor writes this document, and the consumer hooks fall back to plain local
 * state.
 *
 * No JSX here on purpose: the file is named .ts by the plan, so the provider
 * is built with createElement.
 */

import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { doc, onSnapshot, serverTimestamp, setDoc } from "firebase/firestore";
import type { User } from "firebase/auth";
import { toast } from "sonner";
import { db } from "../firebase";
import { shouldPublish, type Participant, type ViewMode } from "../lib/presence";
import {
  MAX_INPUT_LEN,
  SHARED_INPUT_THROTTLE_MS,
  type InputEntry,
} from "../lib/sharedView";

/** Just enough of a participant to label a shared field. Deliberately NOT the
 *  full Participant: `cursor` and `updatedAt` change ~10 times a second and
 *  nothing here has any use for them. */
export type RosterEntry = Pick<Participant, "uid" | "name" | "color">;

export interface SharedViewValue {
  /** Raw remote panel state. Callers must put this through mergeSections,
   *  which is the one tested place that validates it — hence no filtering on
   *  the way in, and hence this cast-shaped type. */
  sections: Record<string, boolean>;
  inputs: Record<string, InputEntry>;
  setSection: (id: string, open: boolean) => void;
  setInput: (id: string, value: string, uid: string) => void;
  publishing: boolean;
  selfUid: string | null;
  /** Identity-stable roster — see `participantRoster` below. */
  participants: RosterEntry[];
}

const EMPTY_SECTIONS: Record<string, boolean> = {};
const EMPTY_INPUTS: Record<string, InputEntry> = {};
const EMPTY_ROSTER: RosterEntry[] = [];

const noop = () => {};

/**
 * Used when no provider is mounted: a purely local dashboard. `publishing`
 * false is what makes the consumer hooks behave as plain useState, so a panel
 * rendered outside a session (or in a unit test) still works.
 */
const FALLBACK: SharedViewValue = {
  sections: EMPTY_SECTIONS,
  inputs: EMPTY_INPUTS,
  setSection: noop,
  setInput: noop,
  publishing: false,
  selfUid: null,
  participants: EMPTY_ROSTER,
};

const SharedViewContext = createContext<SharedViewValue>(FALLBACK);

export function useSharedViewContext(): SharedViewValue {
  return useContext(SharedViewContext);
}

/** What the last snapshot was for, so a session or account switch shows the
 *  new room's state rather than the old one's while the first snapshot of the
 *  new subscription is still in flight. Same shape as usePresence's Snapshot,
 *  and for the same reason: clearing state in an effect is a cascading render
 *  (and a lint error in this codebase). */
interface Snapshot {
  key: string;
  sections: Record<string, boolean>;
  inputs: Record<string, InputEntry>;
}

/** Anything that is not `{ value: string, by: string }` is dropped rather than
 *  handed to a caller that would put it straight into an <input value>. */
function toInputs(raw: unknown): Record<string, InputEntry> {
  if (!raw || typeof raw !== "object") return EMPTY_INPUTS;
  const out: Record<string, InputEntry> = {};
  for (const [id, entry] of Object.entries(raw as Record<string, unknown>)) {
    if (!entry || typeof entry !== "object") continue;
    const { value, by } = entry as { value?: unknown; by?: unknown };
    if (typeof value !== "string" || typeof by !== "string") continue;
    out[id] = { value: value.slice(0, MAX_INPUT_LEN), by };
  }
  return out;
}

/**
 * What a rejected write is called when it is shown to the user.
 *
 * Toasting per WRITE would be unusable: a rules rejection or a dropped
 * connection rejects every keystroke, so a shared field would produce a toast
 * per character. One per kind per provider lifetime says the thing that
 * matters — this is not reaching the room — exactly once.
 */
type FailureKind = "sections" | "inputs";

const FAILURE_MESSAGE: Record<FailureKind, string> = {
  sections: "Panel layout isn't being shared with the session right now.",
  inputs: "Your search text isn't being shared with the session right now.",
};

interface Props {
  sessionCode: string | undefined;
  user: User | null;
  mode: ViewMode;
  /**
   * Threaded in from the EXISTING usePresence subscription (see
   * PresenceLayer) rather than opened again here: two listeners on
   * presence/{code}/participants would double the read cost of the busiest
   * collection in the app to render the same names twice.
   */
  participants: Participant[];
  children: ReactNode;
}

export function SharedViewProvider({
  sessionCode,
  user,
  mode,
  participants,
  children,
}: Props) {
  const [snapshot, setSnapshot] = useState<Snapshot>({
    key: "",
    sections: EMPTY_SECTIONS,
    inputs: EMPTY_INPUTS,
  });

  const publishing = shouldPublish(mode, !!user);
  const selfUid = user?.uid ?? null;
  // The same gate as presence, imported rather than re-derived: a solo or
  // signed-out dashboard does not even subscribe, so its panels and search box
  // are private local state. The mode is in the key so flipping the toggle
  // tears the old subscription down. The separator is a literal `|` rather
  // than a NUL: the three segments are a session code, a uid and
  // "shared"|"solo", none of which can contain one — and a raw U+0000 in the
  // source makes git treat the whole file as binary, so diff, blame and every
  // PR view show nothing at all for the file that owns the auth gate, the
  // subscription and the write path.
  const key = sessionCode && selfUid && publishing ? `${sessionCode}|${selfUid}|${mode}` : "";

  const viewRef = useMemo(
    () => (sessionCode ? doc(db, "presence", sessionCode, "shared", "view") : null),
    [sessionCode],
  );

  useEffect(() => {
    if (!key || !viewRef) return;
    const unsub = onSnapshot(
      viewRef,
      (snap) => {
        const data = snap.data() ?? {};
        setSnapshot({
          key,
          // Not validated here: mergeSections is the single tested place that
          // decides what a malformed panel value means.
          sections: (data.sections ?? EMPTY_SECTIONS) as Record<string, boolean>,
          inputs: toInputs(data.inputs),
        });
      },
      // A shared-view failure must never take the dashboard down: everyone
      // falls back to their own local panel state.
      () => setSnapshot({ key, sections: EMPTY_SECTIONS, inputs: EMPTY_INPUTS }),
    );
    return unsub;
  }, [key, viewRef]);

  // Which failure kinds have already been announced. Same shape and same
  // reason as `throttle` below: mutable, stable for the life of the provider,
  // and reached from callbacks that end up in the context value.
  const [reported] = useState(() => new Set<FailureKind>());

  const reportFailure = useCallback(
    (kind: FailureKind, err: unknown) => {
      // Always logged, so a rejection is diagnosable from the console even
      // after the toast has been shown (and suppressed) once.
      console.warn(`[sharedView] ${kind} write rejected`, err);
      if (reported.has(kind)) return;
      reported.add(kind);
      toast(FAILURE_MESSAGE[kind]);
    },
    [reported],
  );

  const setSection = useCallback(
    (id: string, open: boolean) => {
      if (!publishing || !viewRef) return;
      // Nested merge, verified against the SDK's own parser: set-with-merge
      // builds a LEAF field mask (`sections.<id>`), so this patches one panel
      // and leaves everyone else's open panels alone. Only the server stamps
      // updatedAt — the rules require `== request.time`, exactly as presence
      // does, because a client-chosen time is not evidence of anything.
      void setDoc(
        viewRef,
        { sections: { [id]: open }, updatedAt: serverTimestamp() },
        { merge: true },
        // A rejection is surfaced, not swallowed: a toggle that does not reach
        // the room otherwise looks like a panel that simply did not move.
      ).catch((err) => reportFailure("sections", err));
    },
    [publishing, viewRef, reportFailure],
  );

  // Throttle bookkeeping. A lazily-initialised state value rather than a ref:
  // it is mutable, stable for the life of the provider, and touched only from
  // callbacks — but `setInput` ends up inside the context value, and a
  // callback that captures a ref cannot be handed to another component
  // without tripping react-hooks/refs.
  const [throttle] = useState(() => ({
    lastWrite: new Map<string, number>(),
    pending: new Map<string, InputEntry>(),
    timers: new Map<string, number>(),
  }));

  const writeInput = useCallback(
    (id: string, entry: InputEntry) => {
      if (!viewRef) return;
      void setDoc(
        viewRef,
        { inputs: { [id]: entry }, updatedAt: serverTimestamp() },
        { merge: true },
        // Likewise: a silently rejected input write leaves the typist
        // believing the room can see their query.
      ).catch((err) => reportFailure("inputs", err));
    },
    [viewRef, reportFailure],
  );

  const setInput = useCallback(
    (id: string, value: string, uid: string) => {
      if (!publishing || !viewRef) return;
      // Truncated at the client as well as in the rules: this is a document
      // every viewer re-reads on every keystroke, so an unbounded paste is a
      // bandwidth bill for the whole room.
      const entry: InputEntry = { value: value.slice(0, MAX_INPUT_LEN), by: uid };
      const now = Date.now();
      const wait = SHARED_INPUT_THROTTLE_MS - (now - (throttle.lastWrite.get(id) ?? 0));

      if (wait <= 0) {
        throttle.lastWrite.set(id, now);
        writeInput(id, entry);
        return;
      }

      // Trailing edge, not "drop it". Dropping the tail of a burst — which is
      // what the cursor throttle does — would leave everyone else looking at
      // the query minus its last few characters until the next keystroke.
      throttle.pending.set(id, entry);
      if (throttle.timers.has(id)) return;
      const timer = window.setTimeout(() => {
        throttle.timers.delete(id);
        const latest = throttle.pending.get(id);
        throttle.pending.delete(id);
        if (!latest) return;
        throttle.lastWrite.set(id, Date.now());
        writeInput(id, latest);
      }, wait);
      throttle.timers.set(id, timer);
    },
    [publishing, viewRef, writeInput, throttle],
  );

  useEffect(() => {
    return () => {
      for (const timer of throttle.timers.values()) clearTimeout(timer);
      throttle.timers.clear();
    };
  }, [throttle]);

  /**
   * Cursor ticks must not re-render the panels.
   *
   * `participants` is a fresh array roughly ten times a second — that is the
   * whole reason PresenceLayer exists — so putting it in the context value
   * verbatim would push a new value object at cursor rate and re-render every
   * consumer, undoing that work through a different door.
   *
   * Only uid/name/colour matter here, and those change when someone joins,
   * leaves, or renames — not when they move a mouse. Serialising exactly those
   * fields and deriving the roster BACK from the string gives an object whose
   * identity is stable for as long as its contents are, with the signature as
   * the only dependency, so nothing has to be excluded from a dep array.
   */
  const rosterSig = JSON.stringify(participants.map((p) => [p.uid, p.name, p.color]));
  const participantRoster = useMemo<RosterEntry[]>(
    () =>
      (JSON.parse(rosterSig) as [string, string, string][]).map(([uid, name, color]) => ({
        uid,
        name,
        color,
      })),
    [rosterSig],
  );

  const live = key && snapshot.key === key;
  const sections = live ? snapshot.sections : EMPTY_SECTIONS;
  const inputs = live ? snapshot.inputs : EMPTY_INPUTS;

  const value = useMemo<SharedViewValue>(
    () => ({
      sections,
      inputs,
      setSection,
      setInput,
      publishing,
      selfUid,
      participants: participantRoster,
    }),
    [sections, inputs, setSection, setInput, publishing, selfUid, participantRoster],
  );

  return createElement(SharedViewContext.Provider, { value }, children);
}
