import { describe, expect, it } from "vitest";
import { marquee } from "../src/format";

describe("marquee", () => {
  it("returns short titles unchanged", () => {
    expect(marquee("Short", 0, 9)).toBe("Short");
    expect(marquee("Short", 7, 9)).toBe("Short");
  });

  it("windows long titles from the offset", () => {
    expect(marquee("A Very Long Song Title", 0, 9)).toBe("A Very Lo");
    expect(marquee("A Very Long Song Title", 2, 9)).toBe("Very Long");
  });

  it("wraps around with a gap after the end", () => {
    const t = "ABCDEF"; // padded loop: "ABCDEF   " (len 9)
    expect(marquee(t, 5, 4, 3)).toBe("F   ");
    expect(marquee(t, 8, 4, 3)).toBe(" ABC");
    expect(marquee(t, 9, 4, 3)).toBe(marquee(t, 0, 4, 3)); // full cycle
  });
});
