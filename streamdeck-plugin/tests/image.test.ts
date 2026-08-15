import { describe, expect, it } from "vitest";
import { letterboxSvg } from "../src/image";

/** The wrapper must come back as a data URI, not a raw `<svg>` string:
 *  Stream Deck 7.x on Windows silently ignores raw SVG strings handed to
 *  setImage (the docs still claim support), keeping the manifest glyph with
 *  no error anywhere — the exact "applied but never rendered" bug. Decoding
 *  the URI here proves the SVG inside is still the letterbox we expect. */
function decode(uri: string): string {
  const prefix = "data:image/svg+xml;base64,";
  expect(uri.startsWith(prefix)).toBe(true);
  return Buffer.from(uri.slice(prefix.length), "base64").toString("utf8");
}

describe("letterboxSvg", () => {
  it("returns a base64 SVG data URI, never a raw <svg> string", () => {
    const uri = letterboxSvg("data:image/jpeg;base64,AAA");
    expect(uri).toMatch(/^data:image\/svg\+xml;base64,/);
    expect(decode(uri)).toMatch(/^<svg /);
  });

  it("preserves the aspect ratio of 16:9 artwork on a square key", () => {
    const svg = decode(letterboxSvg("data:image/jpeg;base64,AAA"));
    // "meet" scales to fit INSIDE the square instead of stretching to fill.
    expect(svg).toContain('preserveAspectRatio="xMidYMid meet"');
    expect(svg).toContain("data:image/jpeg;base64,AAA");
  });

  it("fills the letterbox bars rather than leaving them transparent", () => {
    expect(decode(letterboxSvg("data:image/jpeg;base64,AAA"))).toContain("<rect");
  });
});
