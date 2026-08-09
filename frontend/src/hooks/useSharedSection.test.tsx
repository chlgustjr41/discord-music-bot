/**
 * That a panel adopting this hook still works when nobody is sharing.
 *
 * The point of the hook is that seven panels each change one line, so the two
 * claims worth pinning are the ones a panel silently depends on: solo mode
 * behaves exactly like the useState it replaced, and shared mode shows the
 * room's value rather than this browser's.
 */

import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSharedSection } from "./useSharedSection";
import { useSharedViewContext } from "./useSharedView";
import type { SharedViewValue } from "./useSharedView";

vi.mock("./useSharedView", () => ({ useSharedViewContext: vi.fn() }));

const setSection = vi.fn();

function context(over: Partial<SharedViewValue> = {}): SharedViewValue {
  return {
    sections: {},
    inputs: {},
    setSection,
    setInput: vi.fn(),
    publishing: false,
    selfUid: null,
    participants: [],
    ...over,
  };
}

function Panel() {
  const [open, setOpen] = useSharedSection("queue", true);
  return (
    <button type="button" onClick={() => setOpen(!open)}>
      {open ? "open" : "closed"}
    </button>
  );
}

/** A panel that starts closed — the case the solo fallback is about. */
function StatsPanel() {
  const [open, setOpen] = useSharedSection("stats", false);
  return (
    <button type="button" onClick={() => setOpen(!open)}>
      {open ? "open" : "closed"}
    </button>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useSharedSection", () => {
  it("is plain local state when nobody is publishing", () => {
    // The room says closed; a solo viewer must not see that. Asserting the
    // toggle alone would pass even if the hook ignored `publishing` entirely,
    // because an unpublished dashboard's `sections` is usually empty anyway.
    //
    // The write side is NOT asserted here: the auth gate deliberately lives in
    // one place (the provider, where `setSection` is a no-op), so this hook
    // calls it unconditionally. That gate is pinned in useSharedView.test.tsx.
    vi.mocked(useSharedViewContext).mockReturnValue(context({ sections: { queue: false } }));
    render(<Panel />);

    expect(screen.getByRole("button").textContent).toBe("open");
    act(() => screen.getByRole("button").click());
    expect(screen.getByRole("button").textContent).toBe("closed");
  });

  it("shows the room's value, not this browser's, when publishing", () => {
    vi.mocked(useSharedViewContext).mockReturnValue(
      context({ publishing: true, selfUid: "bob", sections: { queue: false } }),
    );
    render(<Panel />);

    // The panel's own default is `true`; the room says otherwise.
    expect(screen.getByRole("button").textContent).toBe("closed");

    act(() => screen.getByRole("button").click());
    expect(setSection).toHaveBeenCalledWith("queue", true);
  });

  it("keeps the panel as the user was last shown it when they flip to solo", () => {
    // Ada opens Stats; Bob never touches it. Flipping to solo must leave Bob
    // looking at the panel he was shown, not snap it shut under him — which
    // is what happens if the local fallback only ever records local clicks.
    vi.mocked(useSharedViewContext).mockReturnValue(
      context({ publishing: true, selfUid: "bob", sections: { stats: true } }),
    );
    const view = render(<StatsPanel />);
    expect(screen.getByRole("button").textContent).toBe("open");

    vi.mocked(useSharedViewContext).mockReturnValue(context({ sections: { stats: true } }));
    act(() => view.rerender(<StatsPanel />));

    expect(screen.getByRole("button").textContent).toBe("open");
  });

  it("falls back to the panel default for a panel nobody has touched", () => {
    vi.mocked(useSharedViewContext).mockReturnValue(
      context({ publishing: true, selfUid: "bob", sections: { stats: false } }),
    );
    render(<Panel />);

    expect(screen.getByRole("button").textContent).toBe("open");
  });
});
