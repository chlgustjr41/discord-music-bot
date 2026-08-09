/**
 * Component-level checks for the presence UI.
 *
 * These exist because the dashboard cannot be loaded in a browser without a
 * live bot session and a real session code, so the pieces are verified
 * directly instead. They pin the behaviours that are load-bearing for the
 * feature's privacy story — the toggle not existing when signed out, the bar
 * disappearing when nobody is there — rather than styling.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CursorLayer } from "./CursorLayer";
import { PresenceBar } from "./PresenceBar";
import { SharedViewToggle } from "./SharedViewToggle";
import type { Participant } from "../lib/presence";

const rect = { left: 0, top: 0, width: 1000, height: 500 } as DOMRect;

function participant(over: Partial<Participant> = {}): Participant {
  return {
    uid: "u1",
    name: "Ada",
    photoURL: null,
    color: "hsl(200, 70%, 60%)",
    cursor: null,
    updatedAt: Date.now(),
    ...over,
  };
}

describe("PresenceBar", () => {
  it("renders nothing when nobody else is here", () => {
    const { container } = render(<PresenceBar participants={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows an initial when the participant has no photo", () => {
    render(<PresenceBar participants={[participant({ name: "ada" })]} />);
    expect(screen.getByText("A")).toBeDefined();
  });

  it("ties each avatar to its cursor colour", () => {
    const { container } = render(
      <PresenceBar participants={[participant({ color: "rgb(1, 2, 3)" })]} />,
    );
    const avatar = container.querySelector('[title="Ada"]') as HTMLElement;
    expect(avatar.style.boxShadow).toContain("rgb(1, 2, 3)");
  });

  it("loads a Google account photo", () => {
    const { container } = render(
      <PresenceBar
        participants={[participant({ photoURL: "https://lh3.googleusercontent.com/a/x" })]}
      />,
    );
    expect(container.querySelector("img")?.getAttribute("src")).toBe(
      "https://lh3.googleusercontent.com/a/x",
    );
  });

  it("never requests an avatar from an arbitrary host", () => {
    // A signed-in participant setting photoURL to their own server turns this
    // <img> into an IP/User-Agent beacon for everyone on the dashboard.
    const { container } = render(
      <PresenceBar
        participants={[participant({ name: "Mallory", photoURL: "https://evil.example/t.gif" })]}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("M")).toBeDefined();
  });

  it("collapses past four into a +N chip rather than overflowing the header", () => {
    const many = Array.from({ length: 7 }, (_, i) =>
      participant({ uid: `u${i}`, name: `User ${i}` }),
    );
    render(<PresenceBar participants={many} />);
    expect(screen.getByText("+3")).toBeDefined();
  });
});

describe("SharedViewToggle", () => {
  it("does not exist for anonymous visitors", () => {
    // They cannot publish presence at all, so offering the choice would lie.
    const { container } = render(
      <SharedViewToggle mode="shared" signedIn={false} onChange={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("switches shared to solo", () => {
    const onChange = vi.fn();
    render(<SharedViewToggle mode="shared" signedIn onChange={onChange} />);
    screen.getByRole("button").click();
    expect(onChange).toHaveBeenCalledWith("solo");
  });

  it("switches solo back to shared", () => {
    const onChange = vi.fn();
    render(<SharedViewToggle mode="solo" signedIn onChange={onChange} />);
    screen.getByRole("button").click();
    expect(onChange).toHaveBeenCalledWith("shared");
  });
});

describe("CursorLayer", () => {
  it("renders nothing before the container has been measured", () => {
    const { container } = render(
      <CursorLayer participants={[participant({ cursor: { x: 0.5, y: 0.5 } })]} rect={null} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for a participant who has not moved yet", () => {
    // Not `querySelector("svg") === null`: any early return satisfies that,
    // including one that still paints an empty fixed overlay.
    const { container } = render(
      <CursorLayer participants={[participant({ cursor: null })]} rect={rect} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the name beside a cursor that has a position", () => {
    render(
      <CursorLayer
        participants={[participant({ cursor: { x: 0.5, y: 0.5 } })]}
        rect={rect}
      />,
    );
    expect(screen.getByText("Ada")).toBeDefined();
  });

  it("places the cursor using the container rect, not the viewport", () => {
    const { container } = render(
      <CursorLayer
        participants={[participant({ cursor: { x: 0.25, y: 0.5 } })]}
        rect={{ left: 100, top: 20, width: 800, height: 400 } as DOMRect}
      />,
    );
    // 100 + 0.25*800 = 300 across, 20 + 0.5*400 = 220 down. Asserted as the
    // actual transform on the positioned element: "the HTML contains 300 and
    // 220 somewhere" also passes with x and y swapped.
    const positioned = container.querySelector<HTMLElement>(".absolute.left-0.top-0");
    expect(positioned).not.toBeNull();
    expect(positioned!.style.transform).toBe("translate3d(300px, 220px, 0)");
  });
});
