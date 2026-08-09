/**
 * The search loop guard, at the wiring layer.
 *
 * lib/sharedView.ts proves that `shouldScheduleSearch` says no to an adopted
 * value. That is only worth anything if SearchPanel actually asks it, and asks
 * it about the right change — the flag is read-and-clear, so a guard wired one
 * effect too early would consume it on the previous render and let the adopted
 * value through anyway.
 *
 * So this test drives the real component and watches the only thing that costs
 * money: the write of `searchQuery` to `servers/{id}`, which is what makes the
 * bot run a search. Ada typing must cost ONE of those across the whole room,
 * not one per dashboard watching.
 */

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { updateDoc } from "firebase/firestore";
import { SearchPanel } from "./SearchPanel";
import { useSharedViewContext } from "../hooks/useSharedView";
import type { SharedViewValue } from "../hooks/useSharedView";

vi.mock("../firebase", () => ({ db: {}, auth: {} }));
vi.mock("firebase/auth", () => ({ onAuthStateChanged: vi.fn(() => () => {}) }));
vi.mock("firebase/firestore", () => ({
  doc: vi.fn(() => ({})),
  updateDoc: vi.fn(async () => {}),
  getDoc: vi.fn(async () => ({ exists: () => false, data: () => ({}) })),
}));
vi.mock("../services/api", () => ({ searchYouTube: vi.fn(async () => []) }));
vi.mock("../lib/social", () => ({ bumpMemberStat: vi.fn() }));
vi.mock("sonner", () => ({ toast: vi.fn() }));
vi.mock("../hooks/useSharedView", () => ({ useSharedViewContext: vi.fn() }));

const setInput = vi.fn();

function context(over: Partial<SharedViewValue> = {}): SharedViewValue {
  return {
    sections: {},
    inputs: {},
    setSection: vi.fn(),
    setInput,
    publishing: true,
    selfUid: "bob",
    participants: [{ uid: "ada", name: "Ada", color: "hsl(1, 2%, 3%)" }],
    ...over,
  };
}

/** Writes that make the bot search, as opposed to any other updateDoc. */
function botSearches(): string[] {
  return vi
    .mocked(updateDoc)
    .mock.calls.map((call) => (call[1] as { searchQuery?: string }).searchQuery)
    .filter((q): q is string => typeof q === "string");
}

function panel() {
  return <SearchPanel serverId="s1" mode="shared" />;
}

function panelWith(over: Partial<React.ComponentProps<typeof SearchPanel>>) {
  return <SearchPanel serverId="s1" mode="shared" {...over} />;
}

const track = {
  videoId: "v1",
  title: "Creep",
  artist: "Radiohead",
  url: "https://youtu.be/v1",
  thumbnail: "",
  duration: 0,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
  vi.mocked(useSharedViewContext).mockReturnValue(context());
});

afterEach(() => {
  vi.useRealTimers();
});

describe("SearchPanel shared query", () => {
  it("searches — and publishes — what this user types", () => {
    render(panel());
    const input = screen.getByRole("textbox");

    act(() => {
      input.focus();
      fireEvent.change(input, { target: { value: "radiohead" } });
    });
    act(() => void vi.advanceTimersByTime(500));

    expect(botSearches()).toEqual(["radiohead"]);
    expect(setInput).toHaveBeenCalledWith("search", "radiohead", "bob");
  });

  it("shows someone else's query without searching it again", () => {
    const view = render(panel());

    vi.mocked(useSharedViewContext).mockReturnValue(
      context({ inputs: { search: { value: "radiohead", by: "ada" } } }),
    );
    act(() => view.rerender(panel()));
    act(() => void vi.advanceTimersByTime(500));

    // Adopted into the box, so everyone sees what is being searched...
    expect(screen.getByRole("textbox")).toHaveValue("radiohead");
    expect(screen.getByText("Ada is typing")).toBeTruthy();
    // ...and NOT sent to the bot a second time, and not echoed back to the
    // room either.
    expect(botSearches()).toEqual([]);
    expect(setInput).not.toHaveBeenCalled();
  });

  it("clears the field for the whole session, and it stays cleared", () => {
    // Clear is a shared action on a shared box: it publishes an empty value
    // rather than only blanking this browser. And it must STICK — the room's
    // copy still holds the old query until that write lands, so an adoption
    // that reacted to the local change would put the text straight back.
    const view = render(panel());
    const input = screen.getByRole("textbox");

    // Search, so that there are results and therefore a Clear button.
    act(() => {
      input.focus();
      fireEvent.change(input, { target: { value: "radiohead" } });
    });
    act(() => void vi.advanceTimersByTime(500));
    act(() => input.blur());
    act(() => view.rerender(panelWith({ searchQuery: null, searchResults: [track] })));
    expect(screen.getByText("Creep")).toBeTruthy();

    // Meanwhile the room's copy of the field is attributed to Ada.
    vi.mocked(useSharedViewContext).mockReturnValue(
      context({ inputs: { search: { value: "radiohead", by: "ada" } } }),
    );
    act(() => view.rerender(panelWith({ searchQuery: null, searchResults: [track] })));

    setInput.mockClear();
    act(() => screen.getByText("Clear").click());
    act(() => void vi.advanceTimersByTime(500));

    expect(setInput).toHaveBeenCalledWith("search", "", "bob");
    expect(screen.getByRole("textbox")).toHaveValue("");
  });

  it("does not cancel your in-flight search when someone else clears the box", () => {
    // The shared field is the TEXT. Ada backspacing to empty empties every
    // box in the room, but it must not reach into another viewer's own
    // request state and kill the spinner on a search they are waiting for.
    const view = render(panel());
    const input = screen.getByRole("textbox");

    act(() => {
      input.focus();
      fireEvent.change(input, { target: { value: "radiohead" } });
    });
    act(() => void vi.advanceTimersByTime(500));
    act(() => input.blur());
    expect(document.querySelector(".animate-spin")).toBeTruthy();

    vi.mocked(useSharedViewContext).mockReturnValue(
      context({ inputs: { search: { value: "", by: "ada" } } }),
    );
    act(() => view.rerender(panel()));
    act(() => void vi.advanceTimersByTime(500));

    expect(screen.getByRole("textbox")).toHaveValue("");
    expect(document.querySelector(".animate-spin")).toBeTruthy();
  });

  it("still searches normally once this user edits the adopted text", () => {
    // The guard answers for exactly one change: adoption must not leave the
    // box permanently unable to search.
    const view = render(panel());
    vi.mocked(useSharedViewContext).mockReturnValue(
      context({ inputs: { search: { value: "radiohead", by: "ada" } } }),
    );
    act(() => view.rerender(panel()));
    act(() => void vi.advanceTimersByTime(500));
    expect(botSearches()).toEqual([]);

    const input = screen.getByRole("textbox");
    act(() => {
      input.focus();
      fireEvent.change(input, { target: { value: "radiohead in rainbows" } });
    });
    act(() => void vi.advanceTimersByTime(500));

    expect(botSearches()).toEqual(["radiohead in rainbows"]);
  });
});
