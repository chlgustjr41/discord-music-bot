import { describe, expect, it } from "vitest";
import {
  PRESENCE_TTL_MS,
  colorForUid,
  livingParticipants,
  movedEnough,
  shouldPublish,
  toNormalized,
  toPixels,
  type Participant,
} from "./presence";

const rect = { left: 100, top: 50, width: 800, height: 400 } as DOMRect;

function p(uid: string, updatedAt: number): Participant {
  return { uid, name: uid, photoURL: null, color: "#fff", cursor: null, updatedAt };
}

describe("colorForUid", () => {
  it("is stable for the same uid", () => {
    expect(colorForUid("abc")).toBe(colorForUid("abc"));
  });

  it("differs across typical uids", () => {
    const uids = ["a", "b", "c", "d", "e", "f", "g", "h"];
    expect(new Set(uids.map(colorForUid)).size).toBeGreaterThan(4);
  });

  it("never returns an empty or transparent colour", () => {
    for (const uid of ["", "x", "averyverylonguid0123456789"]) {
      expect(colorForUid(uid)).toMatch(/^hsl\(/);
    }
  });
});

describe("livingParticipants", () => {
  const now = 10_000;

  it("drops entries older than the TTL and keeps fresh ones", () => {
    const all = [p("fresh", now - 1_000), p("stale", now - PRESENCE_TTL_MS - 1)];
    expect(livingParticipants(all, "me", now).map((x) => x.uid)).toEqual(["fresh"]);
  });

  it("keeps an entry exactly at the TTL boundary", () => {
    const all = [p("edge", now - PRESENCE_TTL_MS)];
    expect(livingParticipants(all, "me", now)).toHaveLength(1);
  });

  it("always excludes yourself — you do not need your own cursor", () => {
    const all = [p("me", now), p("other", now)];
    expect(livingParticipants(all, "me", now).map((x) => x.uid)).toEqual(["other"]);
  });

  it("tolerates a missing or malformed updatedAt rather than throwing", () => {
    const all = [{ ...p("bad", now), updatedAt: undefined as unknown as number }];
    expect(livingParticipants(all, "me", now)).toEqual([]);
  });
});

describe("coordinates", () => {
  it("round-trips a point through normalize and back", () => {
    const norm = toNormalized(500, 250, rect)!;
    const px = toPixels(norm, rect);
    expect(px.x).toBeCloseTo(500);
    expect(px.y).toBeCloseTo(250);
  });

  it("normalizes to 0..1 relative to the rect, not the viewport", () => {
    expect(toNormalized(100, 50, rect)).toEqual({ x: 0, y: 0 });
    expect(toNormalized(900, 450, rect)).toEqual({ x: 1, y: 1 });
  });

  it("returns null outside the rect instead of clamping", () => {
    // Clamping would pin a departed pointer to the edge, which reads as
    // "they are still here, hugging the border".
    expect(toNormalized(99, 250, rect)).toBeNull();
    expect(toNormalized(500, 451, rect)).toBeNull();
  });

  it("returns null for a zero-sized rect rather than dividing by zero", () => {
    expect(toNormalized(0, 0, { left: 0, top: 0, width: 0, height: 0 } as DOMRect)).toBeNull();
  });
});

describe("movedEnough", () => {
  it("suppresses sub-threshold movement", () => {
    expect(movedEnough({ x: 0.5, y: 0.5 }, { x: 0.5005, y: 0.5 }, 8, rect)).toBe(false);
  });

  it("allows movement past the threshold", () => {
    expect(movedEnough({ x: 0.5, y: 0.5 }, { x: 0.53, y: 0.5 }, 8, rect)).toBe(true);
  });

  it("always allows the first point", () => {
    expect(movedEnough(null, { x: 0.5, y: 0.5 }, 8, rect)).toBe(true);
  });
});

describe("shouldPublish", () => {
  it("is the single auth gate: signed out never publishes", () => {
    expect(shouldPublish("shared", false)).toBe(false);
    expect(shouldPublish("solo", false)).toBe(false);
  });

  it("publishes only when signed in AND shared", () => {
    expect(shouldPublish("shared", true)).toBe(true);
    expect(shouldPublish("solo", true)).toBe(false);
  });
});
