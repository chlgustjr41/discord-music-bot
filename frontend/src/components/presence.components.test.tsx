/**
 * Component-level checks for the presence UI.
 *
 * These exist because the dashboard cannot be loaded in a browser without a
 * live bot session and a real session code, so the pieces are verified
 * directly instead. They pin the behaviours that are load-bearing for the
 * feature's privacy story — no avatar request to a host a participant chose,
 * the bar disappearing when nobody is there — rather than styling.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PresenceBar } from "./PresenceBar";
import type { Participant } from "../lib/presence";

function participant(over: Partial<Participant> = {}): Participant {
  return {
    uid: "u1",
    name: "Ada",
    photoURL: null,
    color: "hsl(200, 70%, 60%)",
    focused: true,
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

  it("ties each avatar to its participant colour", () => {
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
