/**
 * The auth gate and the write shape, at the layer that can actually leak.
 *
 * lib/sharedView.ts proves the conflict rules; it says nothing about whether
 * this provider writes when it must not. Replacing `shouldPublish(mode, !!user)`
 * with `true` here would leave every pure test green while a solo — or
 * signed-out — dashboard published its search box into the shared room.
 *
 * Firestore is mocked rather than emulated: the claim is about which calls are
 * made, not what the server does with them.
 */

import { useEffect } from "react";
import { render, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { User } from "firebase/auth";
import { doc, onSnapshot, serverTimestamp, setDoc } from "firebase/firestore";
import { toast } from "sonner";
import {
  SharedViewProvider,
  useSharedViewContext,
  type SharedViewValue,
} from "./useSharedView";
import { MAX_INPUT_LEN } from "../lib/sharedView";
import type { Participant } from "../lib/presence";

vi.mock("../firebase", () => ({ db: {} }));

vi.mock("sonner", () => ({ toast: vi.fn() }));

vi.mock("firebase/firestore", () => ({
  doc: vi.fn((...path: unknown[]) => ({ __type: "doc", path })),
  onSnapshot: vi.fn(() => () => {}),
  setDoc: vi.fn(async () => {}),
  serverTimestamp: vi.fn(() => ({ __type: "serverTimestamp" })),
}));

const signedIn = { uid: "u1", displayName: "Ada", photoURL: null } as User;

/** Reports the context value out through a prop, in an effect. Assigning to
 *  an outer variable during render is a lint error in this codebase — and the
 *  effect only fires when the value's IDENTITY changes, which is exactly the
 *  thing the roster tests below are about. */
function Probe({ onValue }: { onValue: (v: SharedViewValue) => void }) {
  const value = useSharedViewContext();
  useEffect(() => onValue(value), [onValue, value]);
  return null;
}

function participant(over: Partial<Participant> = {}): Participant {
  return {
    uid: "u2", name: "Bob", photoURL: null, color: "hsl(1, 2%, 3%)",
    cursor: null, updatedAt: 0, ...over,
  };
}

/** Every distinct context value the provider has published, oldest first. */
function harness(user: User | null, mode: "shared" | "solo", people: Participant[] = []) {
  const seen: SharedViewValue[] = [];
  const onValue = (v: SharedViewValue) => void seen.push(v);
  const tree = (mode: "shared" | "solo", people: Participant[]) => (
    <SharedViewProvider sessionCode="ABC123" user={user} mode={mode} participants={people}>
      <Probe onValue={onValue} />
    </SharedViewProvider>
  );
  const view = render(tree(mode, people));
  return {
    seen,
    latest: () => seen[seen.length - 1],
    update: (nextPeople: Participant[]) => view.rerender(tree(mode, nextPeople)),
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  // clearAllMocks clears CALLS, not implementations, so a rejecting setDoc
  // from the failure tests below would leak into every later test.
  vi.mocked(setDoc).mockResolvedValue(undefined);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("SharedViewProvider auth gate", () => {
  it("neither reads nor writes for an anonymous visitor", () => {
    const h = harness(null, "shared");
    act(() => h.latest().setSection("queue", true));
    act(() => h.latest().setInput("search", "radiohead", "u1"));

    expect(onSnapshot).not.toHaveBeenCalled();
    expect(setDoc).not.toHaveBeenCalled();
    expect(h.latest().publishing).toBe(false);
  });

  it("neither reads nor writes in solo mode", () => {
    // Solo is symmetric with presence: your panels are yours, and you do not
    // even see the room's.
    const h = harness(signedIn, "solo");
    act(() => h.latest().setSection("queue", true));
    act(() => h.latest().setInput("search", "radiohead", "u1"));

    expect(onSnapshot).not.toHaveBeenCalled();
    expect(setDoc).not.toHaveBeenCalled();
    expect(h.latest().publishing).toBe(false);
  });

  it("subscribes to exactly one shared document when publishing", () => {
    const h = harness(signedIn, "shared");

    expect(h.latest().publishing).toBe(true);
    expect(onSnapshot).toHaveBeenCalledTimes(1);
    expect(vi.mocked(doc).mock.calls[0].slice(1)).toEqual([
      "presence",
      "ABC123",
      "shared",
      "view",
    ]);
  });
});

describe("SharedViewProvider writes", () => {
  it("patches one panel with a nested merge, server-stamped", () => {
    const h = harness(signedIn, "shared");
    act(() => h.latest().setSection("queue", false));

    const [, data, options] = vi.mocked(setDoc).mock.calls[0];
    // Nested, not `{ "sections.queue": false }` and not a whole-map replace:
    // set-with-merge produces a leaf field mask, so other panels survive.
    expect(data).toEqual({
      sections: { queue: false },
      updatedAt: { __type: "serverTimestamp" },
    });
    expect(options).toEqual({ merge: true });
    expect(serverTimestamp).toHaveBeenCalled();
  });

  it("truncates a pasted wall of text before it reaches the room", () => {
    const h = harness(signedIn, "shared");
    act(() => h.latest().setInput("search", "x".repeat(5000), "u1"));

    const data = vi.mocked(setDoc).mock.calls[0][1] as {
      inputs: Record<string, { value: string }>;
    };
    expect(data.inputs.search.value).toHaveLength(MAX_INPUT_LEN);
  });

  it("throttles a burst of keystrokes but still publishes the last one", () => {
    // Dropping the tail would leave the room looking at the query minus its
    // final characters until the next keystroke.
    const h = harness(signedIn, "shared");
    const { setInput } = h.latest();

    act(() => {
      setInput("search", "r", "u1");
      setInput("search", "ra", "u1");
      setInput("search", "rad", "u1");
    });
    expect(setDoc).toHaveBeenCalledTimes(1);

    act(() => void vi.advanceTimersByTime(400));
    expect(setDoc).toHaveBeenCalledTimes(2);

    const data = vi.mocked(setDoc).mock.calls[1][1] as { inputs: Record<string, unknown> };
    expect(data.inputs).toEqual({ search: { value: "rad", by: "u1" } });
  });
});

describe("SharedViewProvider write failures", () => {
  // A rejected write used to be swallowed whole: the panel did not move, and
  // there was no toast, no console entry and nothing to tell the user their
  // click had not reached the room.
  function rejectWrites() {
    vi.mocked(setDoc).mockRejectedValue(new Error("permission-denied"));
  }

  it("tells the user when a panel toggle does not reach the room", async () => {
    rejectWrites();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const h = harness(signedIn, "shared");

    await act(async () => h.latest().setSection("queue", false));

    expect(toast).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("says it once, not once per keystroke", async () => {
    // A rules rejection rejects EVERY write, so a toast per write would bury
    // the dashboard under one notification per character typed.
    rejectWrites();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const h = harness(signedIn, "shared");
    const { setInput } = h.latest();

    await act(async () => setInput("search", "r", "u1"));
    await act(async () => void vi.advanceTimersByTime(400));
    await act(async () => setInput("search", "ra", "u1"));
    await act(async () => void vi.advanceTimersByTime(400));

    expect(vi.mocked(setDoc).mock.calls.length).toBeGreaterThan(1);
    expect(toast).toHaveBeenCalledTimes(1);
    // Every rejection is still logged, so the second one is diagnosable.
    expect(warn.mock.calls.length).toBeGreaterThan(1);
    warn.mockRestore();
  });

  it("says nothing while writes are landing", async () => {
    const h = harness(signedIn, "shared");
    await act(async () => h.latest().setSection("queue", false));

    expect(toast).not.toHaveBeenCalled();
  });
});

describe("SharedViewProvider roster", () => {
  it("keeps the context value stable while only cursors move", () => {
    // This is the whole reason PresenceLayer exists. A new context value at
    // cursor rate would re-render every panel that reads it.
    const h = harness(signedIn, "shared", [participant({ cursor: { x: 0.1, y: 0.1 } })]);
    expect(h.seen).toHaveLength(1);

    act(() => h.update([participant({ cursor: { x: 0.9, y: 0.9 } })]));

    expect(h.seen).toHaveLength(1);
  });

  it("produces a new value when somebody actually joins", () => {
    const h = harness(signedIn, "shared", [participant()]);

    act(() => h.update([participant(), participant({ uid: "u3", name: "Cy" })]));

    expect(h.seen).toHaveLength(2);
    expect(h.latest().participants.map((p) => p.name)).toEqual(["Bob", "Cy"]);
  });
});
