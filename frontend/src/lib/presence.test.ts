import { describe, expect, it } from "vitest";
import {
  PRESENCE_TTL_MS,
  colorForUid,
  isAllowedPhotoUrl,
  livingParticipants,
  shouldPublish,
  type Participant,
} from "./presence";

function p(uid: string, updatedAt: number): Participant {
  return { uid, name: uid, photoURL: null, color: "#fff", updatedAt };
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
    expect(livingParticipants(all, now).map((x) => x.uid)).toEqual(["fresh"]);
  });

  it("keeps an entry exactly at the TTL boundary", () => {
    const all = [p("edge", now - PRESENCE_TTL_MS)];
    expect(livingParticipants(all, now)).toHaveLength(1);
  });

  it("keeps yourself — you are shown in the bar alongside everyone else", () => {
    // The inverse of the rule this replaced. Self used to be filtered out
    // here because the only thing presence drew was other people's cursors;
    // now the bar includes you, so dropping self would hide you from it and
    // liveness would be silently deciding a rendering question.
    const all = [p("me", now), p("other", now)];
    expect(livingParticipants(all, now).map((x) => x.uid)).toEqual(["me", "other"]);
  });

  it("tolerates a missing or malformed updatedAt rather than throwing", () => {
    const all = [{ ...p("bad", now), updatedAt: undefined as unknown as number }];
    expect(livingParticipants(all, now)).toEqual([]);
  });

  it("treats a not-yet-acked local write (null updatedAt) as not living", () => {
    // serverTimestamp() reads back as null until the server acks it.
    const all = [{ ...p("pending", now), updatedAt: null as unknown as number }];
    expect(livingParticipants(all, now)).toEqual([]);
  });

  it("refuses a far-future timestamp instead of reading it as freshest possible", () => {
    // Belt-and-braces behind the `updatedAt == request.time` rule: a ghost
    // stamped a year ahead must not be permanently alive if a rule is relaxed.
    const all = [p("ghost", now + 1e12), p("ghost2", now + PRESENCE_TTL_MS + 1)];
    expect(livingParticipants(all, now)).toEqual([]);
  });

  it("survives a modestly skewed VIEWER clock rather than showing nobody", () => {
    // updatedAt is now the server's clock and `now` is still this browser's,
    // so a viewer running a few seconds slow sees every live entry as
    // slightly future-dated. Rejecting those would blank the bar for them.
    const all = [p("live", now + 3_000)];
    expect(livingParticipants(all, now).map((x) => x.uid)).toEqual(["live"]);
  });
});

describe("shouldPublish", () => {
  it("is the single auth gate: signed out never publishes", () => {
    expect(shouldPublish(false)).toBe(false);
  });

  it("publishes when signed in", () => {
    expect(shouldPublish(true)).toBe(true);
  });
});

describe("isAllowedPhotoUrl", () => {
  it("accepts the Google account photo hosts", () => {
    expect(isAllowedPhotoUrl("https://lh3.googleusercontent.com/a/ACg8ocK=s96-c")).toBe(true);
    // Google has served avatars from lh4/lh5/lh6 as well, so the host is
    // matched by suffix rather than pinned to lh3.
    expect(isAllowedPhotoUrl("https://lh5.googleusercontent.com/a-/AOh14=s96-c")).toBe(true);
  });

  it("rejects an attacker-controlled beacon", () => {
    // This is the whole point: photoURL is rendered as <img src> for every
    // viewer, so an arbitrary host harvests everyone's IP and User-Agent on
    // every render with no interaction.
    expect(isAllowedPhotoUrl("https://evil.example/track.gif")).toBe(false);
    expect(isAllowedPhotoUrl("http://lh3.googleusercontent.com/a/x")).toBe(false);
  });

  it("is not fooled by lookalike hosts", () => {
    expect(isAllowedPhotoUrl("https://evil-googleusercontent.com/x")).toBe(false);
    expect(isAllowedPhotoUrl("https://googleusercontent.com.evil.example/x")).toBe(false);
    expect(isAllowedPhotoUrl("https://lh3.googleusercontent.com@evil.example/x")).toBe(false);
  });

  it("rejects non-http schemes and junk rather than throwing", () => {
    expect(isAllowedPhotoUrl("javascript:alert(1)")).toBe(false);
    expect(isAllowedPhotoUrl("data:image/svg+xml,<svg/>")).toBe(false);
    expect(isAllowedPhotoUrl("not a url")).toBe(false);
    expect(isAllowedPhotoUrl(null)).toBe(false);
    expect(isAllowedPhotoUrl("")).toBe(false);
  });
});
