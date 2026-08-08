import { describe, expect, it } from "vitest";
import { letterboxSvg } from "../src/image";

describe("letterboxSvg", () => {
  it("preserves the aspect ratio of 16:9 artwork on a square key", () => {
    const svg = letterboxSvg("data:image/jpeg;base64,AAA");
    // "meet" scales to fit INSIDE the square instead of stretching to fill.
    expect(svg).toContain('preserveAspectRatio="xMidYMid meet"');
    expect(svg).toContain("data:image/jpeg;base64,AAA");
    expect(svg).toMatch(/^<svg /);
  });

  it("fills the letterbox bars rather than leaving them transparent", () => {
    expect(letterboxSvg("data:image/jpeg;base64,AAA")).toContain("<rect");
  });
});
