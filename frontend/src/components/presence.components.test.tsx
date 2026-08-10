/**
 * Component-level checks for the presence UI.
 *
 * These exist because the dashboard cannot be loaded in a browser without a
 * live bot session and a real session code, so the pieces are verified
 * directly instead. They pin the behaviours that are load-bearing for the
 * feature's privacy story — no avatar request to a host a participant chose,
 * the bar disappearing when nobody is there — rather than styling.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PresenceBar } from "./PresenceBar";
import type { Participant } from "../lib/presence";

// The nickname dialog reaches Firebase auth and localStorage through
// identity.ts and useAuth; the bar's contract with it is just "it opens".
vi.mock("../lib/identity", () => ({
  useIdentity: () => ({
    name: "Ada",
    named: true,
    viaAccount: true,
    signedIn: true,
    nickname: "",
    accountName: "Ada",
  }),
  setNickname: vi.fn(),
}));

vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({ signInWithGoogle: vi.fn(), logout: vi.fn() }),
}));

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
    const avatar = container.querySelector('[data-uid="u1"]') as HTMLElement;
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

describe("PresenceBar focus state", () => {
  it("visibly distinguishes someone who is not looking at the page", () => {
    // "Here but not looking" has to read at a glance, next to a focused
    // avatar — the whole point of publishing the focus flag.
    const { container } = render(
      <PresenceBar
        participants={[
          participant({ uid: "here", focused: true }),
          participant({ uid: "away", focused: false }),
        ]}
      />,
    );
    const here = container.querySelector('[data-uid="here"]') as HTMLElement;
    const away = container.querySelector('[data-uid="away"]') as HTMLElement;

    expect(away.getAttribute("data-focused")).toBe("false");
    expect(here.getAttribute("data-focused")).toBe("true");
    // Two independent signals, because opacity alone is easy to miss against
    // a busy header and desaturation alone is invisible on a letter avatar.
    expect(away.className).toMatch(/grayscale/);
    expect(away.className).toMatch(/opacity-/);
    expect(here.className).not.toMatch(/grayscale/);
    expect(here.className).not.toMatch(/opacity-/);
  });

  it("does not grey anyone out merely for lacking the flag", () => {
    const { container } = render(
      <PresenceBar participants={[participant({ uid: "legacy", focused: true })]} />,
    );
    expect(
      (container.querySelector('[data-uid="legacy"]') as HTMLElement).className,
    ).not.toMatch(/grayscale/);
  });
});

describe("PresenceBar hover", () => {
  it("keeps the name out of the way until you hover", () => {
    render(<PresenceBar participants={[participant({ name: "Ada Lovelace" })]} />);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("reveals the display name on hover", () => {
    const { container } = render(
      <PresenceBar participants={[participant({ name: "Ada Lovelace" })]} />,
    );
    fireEvent.mouseEnter(container.querySelector('[data-uid="u1"]') as HTMLElement);
    expect(within(screen.getByRole("tooltip")).getByText("Ada Lovelace")).toBeDefined();
  });

  it("says an unfocused participant is away, not merely who they are", () => {
    const { container } = render(
      <PresenceBar participants={[participant({ name: "Ada", focused: false })]} />,
    );
    fireEvent.mouseEnter(container.querySelector('[data-uid="u1"]') as HTMLElement);
    expect(screen.getByRole("tooltip").textContent).toMatch(/away/i);
  });

  it("hides the name again when the pointer leaves", () => {
    const { container } = render(<PresenceBar participants={[participant()]} />);
    const avatar = container.querySelector('[data-uid="u1"]') as HTMLElement;
    fireEvent.mouseEnter(avatar);
    fireEvent.mouseLeave(avatar);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("reveals the name on keyboard focus too, not only on hover", () => {
    const { container } = render(<PresenceBar participants={[participant()]} />);
    fireEvent.focus(container.querySelector('[data-uid="u1"]') as HTMLElement);
    expect(screen.getByRole("tooltip")).toBeDefined();
  });
});

describe("PresenceBar self", () => {
  it("marks your own avatar so you can tell which one is you without hovering", () => {
    const { container } = render(
      <PresenceBar
        selfId="u1"
        participants={[participant({ uid: "u1" }), participant({ uid: "u2" })]}
      />,
    );
    const self = container.querySelector('[data-uid="u1"]') as HTMLElement;
    const other = container.querySelector('[data-uid="u2"]') as HTMLElement;

    expect(self.getAttribute("data-self")).toBe("true");
    expect(other.getAttribute("data-self")).toBeNull();
    expect(self.className).not.toBe(other.className);
    expect(self.getAttribute("aria-label")).toMatch(/you/i);
  });

  it("makes your own avatar a button, and nobody else's", () => {
    const { container } = render(
      <PresenceBar
        selfId="u1"
        participants={[participant({ uid: "u1" }), participant({ uid: "u2" })]}
      />,
    );
    expect((container.querySelector('[data-uid="u1"]') as HTMLElement).tagName).toBe(
      "BUTTON",
    );
    expect((container.querySelector('[data-uid="u2"]') as HTMLElement).tagName).not.toBe(
      "BUTTON",
    );
  });

  it("opens the nickname editor when you click yourself", () => {
    const { container } = render(
      <PresenceBar selfId="u1" participants={[participant({ uid: "u1" })]} />,
    );
    fireEvent.click(container.querySelector('[data-uid="u1"]') as HTMLElement);
    expect(screen.getByText("Who are you?")).toBeDefined();
  });

  it("lets a signed-out visitor click their own badge", () => {
    // Self-detection keys on the PRESENCE id, not a uid. Keying on the uid
    // would leave every anonymous visitor unable to name themselves — the one
    // thing they can do about being "Anonymous 2".
    const id = "anon_3f7a9c21-4e5b-4c8d-9a1e-77b0c2d3e4f5";
    const { container } = render(
      <PresenceBar
        selfId={id}
        participants={[participant({ uid: id, name: "Anonymous 1" })]}
      />,
    );
    const self = container.querySelector(`[data-uid="${id}"]`) as HTMLElement;
    expect(self.tagName).toBe("BUTTON");
    fireEvent.click(self);
    expect(screen.getByText("Who are you?")).toBeDefined();
  });

  it("does not open the editor when you click someone else", () => {
    const { container } = render(
      <PresenceBar selfId="u1" participants={[participant({ uid: "u2" })]} />,
    );
    fireEvent.click(container.querySelector('[data-uid="u2"]') as HTMLElement);
    expect(screen.queryByText("Who are you?")).toBeNull();
  });
});
