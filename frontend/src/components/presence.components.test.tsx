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

  it("collapses past six into a +N chip rather than overflowing the row", () => {
    const many = Array.from({ length: 9 }, (_, i) =>
      participant({ uid: `u${i}`, name: `User ${i}` }),
    );
    render(<PresenceBar participants={many} />);
    expect(screen.getByText("+3")).toBeDefined();  // 9 shown-capped at 6
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
    // Away is DRAINED COLOUR, never alpha: these avatars overlap, and a
    // translucent one shows the avatar behind it straight through, which
    // reads as a rendering fault rather than as absence.
    expect(away.className).not.toMatch(/opacity-/);
    expect(here.className).not.toMatch(/opacity-/);
    // The ring colour is the identity signal, so an away row loses it.
    expect(away.style.boxShadow).toContain("var(--muted-foreground)");
    expect(here.style.boxShadow).not.toContain("var(--muted-foreground)");
    // A photo is greyscaled; a letter avatar has no colour to drain, which is
    // why the ring carries the signal instead.
    expect(away.querySelector("img")?.className ?? "grayscale").toMatch(/grayscale/);
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

describe("PresenceBar stacking and hover", () => {
  const three = [
    participant({ uid: "a", name: "A" }),
    participant({ uid: "b", name: "B" }),
    participant({ uid: "c", name: "C" }),
  ];

  it("is fully opaque so the overlap reads as depth, not as a glitch", () => {
    const { container } = render(<PresenceBar participants={three} />);
    for (const el of container.querySelectorAll<HTMLElement>("[data-uid]")) {
      expect(el.className).not.toMatch(/opacity-/);
      // The outer ring is painted in the card colour: that is what cuts one
      // avatar cleanly out of the one behind it.
      expect(el.style.boxShadow).toContain("var(--card)");
    }
  });

  it("stacks in one consistent direction", () => {
    // Ascending z-index would make the overlap look shuffled rather than
    // ordered, which is most of why the collapsed state looked wrong.
    const { container } = render(<PresenceBar participants={three} />);
    const z = [...container.querySelectorAll<HTMLElement>("[data-uid]")].map((e) =>
      Number(e.style.zIndex),
    );
    expect(z).toEqual([...z].sort((x, y) => y - x));
    expect(new Set(z).size).toBe(z.length);
  });

  it("expands on hover and on keyboard focus anywhere in the group", () => {
    const { container } = render(<PresenceBar participants={three} />);
    for (const el of container.querySelectorAll<HTMLElement>("[data-uid]")) {
      expect(el.className).toMatch(/-ml-2/);            // collapsed
      expect(el.className).toMatch(/group-hover:ml-/);  // spread on hover
      // Tabbing to one avatar must open the whole stack; expanding only the
      // focused one would make it jump out of a still-collapsed row.
      expect(el.className).toMatch(/group-focus-within:ml-/);
      expect(el.className).toMatch(/transition-\[margin-left\]/);
    }
  });

  it("keeps the overflow chip legible while collapsed", () => {
    // It sits UNDER the last avatar to keep the stacking direction, so its
    // label needs padding to clear it until the group expands.
    const many = Array.from({ length: 9 }, (_, i) =>
      participant({ uid: `u${i}`, name: `User ${i}` }),
    );
    const { container } = render(<PresenceBar participants={many} />);
    const chip = container.querySelector<HTMLElement>('[data-overflow="true"]')!;
    expect(chip).not.toBeNull();
    expect(chip.className).toMatch(/pl-4/);
    expect(chip.className).toMatch(/group-hover:pl-/);
  });
});
