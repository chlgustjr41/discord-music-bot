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

  it("falls back to the panel default for a panel nobody has touched", () => {
    vi.mocked(useSharedViewContext).mockReturnValue(
      context({ publishing: true, selfUid: "bob", sections: { stats: false } }),
    );
    render(<Panel />);

    expect(screen.getByRole("button").textContent).toBe("open");
  });
});
