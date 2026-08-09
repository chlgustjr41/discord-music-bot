/**
 * The adoption guard, at the wiring layer.
 *
 * lib/sharedView.ts proves WHEN a remote value should be adopted. It says
 * nothing about whether the caller can tell an adopted change apart from a
 * keystroke — and that distinction is the whole reason `consumeAdopted`
 * exists: SearchPanel searches on every change, so an adopted value that
 * looked local would make every viewer fire (and publish) the same search.
 *
 * The provider is mocked out; this is about the hook's contract, not Firestore.
 */

import { useState } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSharedInput } from "./useSharedInput";
import { useSharedViewContext } from "./useSharedView";
import type { SharedViewValue } from "./useSharedView";

vi.mock("./useSharedView", () => ({ useSharedViewContext: vi.fn() }));

const setInput = vi.fn();

function context(over: Partial<SharedViewValue> = {}): SharedViewValue {
  return {
    sections: {},
    inputs: {},
    setSection: vi.fn(),
    setInput,
    publishing: true,
    selfUid: "bob",
    participants: [{ uid: "ada", name: "Ada", color: "rgb(1, 2, 3)" }],
    ...over,
  };
}

/** Every change the field has seen, tagged with whether it was adopted. */
const changes: { value: string; adopted: boolean }[] = [];

function Field() {
  const [value, setValue] = useState("");
  const shared = useSharedInput("search", value, setValue);

  // Stands in for SearchPanel's search-on-change effect, minus the network.
  const last = changes[changes.length - 1];
  if (!last || last.value !== value) {
    changes.push({ value, adopted: shared.consumeAdopted() });
  }

  return (
    <div>
      <input
        aria-label="q"
        value={value}
        onFocus={shared.onFocus}
        onBlur={shared.onBlur}
        onChange={(e) => {
          setValue(e.target.value);
          shared.publish(e.target.value);
        }}
      />
      <span data-testid="typist">{shared.typist ? shared.typist.name : "-"}</span>
    </div>
  );
}

beforeEach(() => {
  changes.length = 0;
  vi.clearAllMocks();
  vi.mocked(useSharedViewContext).mockReturnValue(context());
});

describe("useSharedInput adoption", () => {
  it("flags a value that arrived from someone else, exactly once", () => {
    const view = render(<Field />);
    expect(changes).toEqual([{ value: "", adopted: false }]);

    vi.mocked(useSharedViewContext).mockReturnValue(
      context({ inputs: { search: { value: "radiohead", by: "ada" } } }),
    );
    act(() => view.rerender(<Field />));

    // Adopted, and reported as adopted — otherwise Bob's browser fires Ada's
    // search, publishes it, and the bot answers it a second time.
    expect(changes[changes.length - 1]).toEqual({ value: "radiohead", adopted: true });

    // Read-and-clear: the flag answers for one change and then stops.
    act(() => view.rerender(<Field />));
    expect(changes.filter((c) => c.adopted)).toHaveLength(1);
  });

  it("does not flag the user's own typing, and publishes it", () => {
    render(<Field />);
    const input = screen.getByLabelText<HTMLInputElement>("q");

    act(() => {
      input.focus();
      fireEvent.change(input, { target: { value: "pixies" } });
    });

    expect(changes[changes.length - 1]).toEqual({ value: "pixies", adopted: false });
    expect(setInput).toHaveBeenCalledWith("search", "pixies", "bob");
  });

  it("leaves a focused field alone until it is blurred", () => {
    const view = render(<Field />);
    const input = screen.getByLabelText<HTMLInputElement>("q");
    act(() => input.focus());

    vi.mocked(useSharedViewContext).mockReturnValue(
      context({ inputs: { search: { value: "radiohead", by: "ada" } } }),
    );
    act(() => view.rerender(<Field />));
    expect(input.value).toBe("");

    act(() => input.blur());
    expect(input.value).toBe("radiohead");
  });

  it("names the person typing in the field", () => {
    const view = render(<Field />);
    expect(screen.getByTestId("typist").textContent).toBe("-");

    vi.mocked(useSharedViewContext).mockReturnValue(
      context({ inputs: { search: { value: "radiohead", by: "ada" } } }),
    );
    act(() => view.rerender(<Field />));

    expect(screen.getByTestId("typist").textContent).toBe("Ada");
  });
});
