import { describe, expect, it } from "vitest";
import { isEmbedded, refusesToRender } from "./framing";

/** Only `top` and `self` are read, so plain objects stand in for windows. */
function win(top: unknown, self: unknown) {
  return { top, self } as unknown as Window;
}

describe("isEmbedded", () => {
  it("is false for a top-level window", () => {
    const w = {};
    expect(isEmbedded(win(w, w))).toBe(false);
  });

  it("is true when another window is on top", () => {
    expect(isEmbedded(win({}, {}))).toBe(true);
  });

  it("is true when the browser will not say who is on top", () => {
    const hostile = {
      get top(): Window {
        throw new DOMException("blocked", "SecurityError");
      },
      self: {},
    } as unknown as Window;
    expect(isEmbedded(hostile)).toBe(true);
  });
});

describe("refusesToRender", () => {
  const top = {};

  it("renders normally at top level, in development and in production", () => {
    expect(refusesToRender(win(top, top), false)).toBe(false);
    expect(refusesToRender(win(top, top), true)).toBe(false);
  });

  it("refuses to render inside a frame in production", () => {
    expect(refusesToRender(win({}, {}), false)).toBe(true);
  });

  it("still renders inside a frame in development, where preview panes embed the dev server", () => {
    expect(refusesToRender(win({}, {}), true)).toBe(false);
  });
});
