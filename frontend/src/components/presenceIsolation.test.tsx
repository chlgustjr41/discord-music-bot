/**
 * That mounting SharedViewProvider inside PresenceLayer did not put the panels
 * back in the cursor render path.
 *
 * PresenceLayer exists so that remote cursors — tens of snapshots a second —
 * re-render only themselves. The provider needs the participant roster too, so
 * it is mounted here rather than in Dashboard; the thing that keeps that honest
 * is that the panels arrive as a `children` element Dashboard created, and that
 * the provider's context value is memoised on a roster signature which ignores
 * cursors.
 *
 * That is invisible in the UI and would rot silently, so it is asserted
 * directly: a cursor tick must not re-render a panel, while somebody joining or
 * renaming must.
 */

import { useState, type ReactNode } from "react";
import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PresenceLayer, type PublishCursor } from "./PresenceLayer";
import { usePresence } from "../hooks/usePresence";
import { useSharedViewContext } from "../hooks/useSharedView";
import type { Participant } from "../lib/presence";

vi.mock("../firebase", () => ({ db: {} }));
vi.mock("firebase/firestore", () => ({
  doc: vi.fn(() => ({})),
  onSnapshot: vi.fn(() => () => {}),
  serverTimestamp: vi.fn(() => "ts"),
  setDoc: vi.fn(async () => {}),
}));
vi.mock("../hooks/usePresence", () => ({ usePresence: vi.fn() }));

function participant(over: Partial<Participant> = {}): Participant {
  return {
    uid: "ada",
    name: "Ada",
    photoURL: null,
    color: "hsl(1, 2%, 3%)",
    cursor: { x: 0.5, y: 0.5 },
    updatedAt: Date.now(),
    ...over,
  };
}

/** Pushes a new roster into the mounted PresenceLayer WITHOUT re-rendering its
 *  parent — which is the whole point: in the real app the roster arrives from a
 *  Firestore snapshot inside usePresence, not as a prop from Dashboard. */
let emit: ((rows: Participant[]) => void) | null = null;

let panelRenders = 0;

function Panel() {
  panelRenders++;
  const { participants } = useSharedViewContext();
  return <p data-testid="panel">{participants.map((p) => p.name).join(",") || "-"}</p>;
}

function Host({ children }: { children: ReactNode }) {
  return (
    <PresenceLayer
      sessionCode="ABC123"
      user={{ uid: "bob" } as never}
      mode="shared"
      containerRef={{ current: null }}
      cursorRef={{ current: null } as React.RefObject<PublishCursor | null>}
      barSlot={null}
    >
      {children}
    </PresenceLayer>
  );
}

beforeEach(() => {
  panelRenders = 0;
  emit = null;
  vi.clearAllMocks();
  vi.mocked(usePresence).mockImplementation(() => {
    const [participants, setParticipants] = useState<Participant[]>([participant()]);
    emit = setParticipants;
    return { participants, publishCursor: vi.fn(), publishing: true };
  });
});

describe("PresenceLayer + SharedViewProvider", () => {
  it("does not re-render the panels when a cursor moves", () => {
    // Created once, exactly as Dashboard creates it in a render that cursor
    // ticks do not reach.
    const panels = <Panel />;
    render(<Host>{panels}</Host>);

    expect(panelRenders).toBe(1);
    expect(screen.getByTestId("panel").textContent).toBe("Ada");

    // Ten cursor updates — the same person, somewhere else on the page.
    for (let i = 1; i <= 10; i++) {
      act(() => emit!([participant({ cursor: { x: i / 20, y: 0.5 } })]));
    }

    expect(panelRenders).toBe(1);
  });

  it("does re-render them when the roster itself changes", () => {
    // The other half of the claim: the provider is genuinely wired, not inert.
    const panels = <Panel />;
    render(<Host>{panels}</Host>);
    expect(panelRenders).toBe(1);

    act(() => emit!([participant({ name: "Ada Lovelace" })]));
    expect(panelRenders).toBe(2);
    expect(screen.getByTestId("panel").textContent).toBe("Ada Lovelace");

    act(() => emit!([participant(), participant({ uid: "cy", name: "Cy" })]));
    expect(screen.getByTestId("panel").textContent).toBe("Ada,Cy");
  });
});
