import { describe, expect, it } from "vitest";
import {
  PRESENCE_TTL_MS,
  anchorFor,
  colorForUid,
  isAllowedPhotoUrl,
  isFocused,
  livingParticipants,
  shouldPublish,
  toParticipant,
  type Participant,
} from "./presence";

function p(uid: string, updatedAt: number): Participant {
  return { uid, name: uid, photoURL: null, color: "#fff", focused: true, updatedAt };
}

describe("isFocused", () => {
  // The full truth table, because the interesting half is the pair that a
  // single-signal implementation gets wrong: a visible tab in a window you
  // have alt-tabbed away from is not being read, and a focused window whose
  // tab is hidden is showing something else entirely.
  it("is true only when the tab is visible AND the window has focus", () => {
    expect(isFocused("visible", true)).toBe(true);
  });

  it("is false for a visible tab in an unfocused window", () => {
    expect(isFocused("visible", false)).toBe(false);
  });

  it("is false for a hidden tab in a focused window", () => {
    expect(isFocused("hidden", true)).toBe(false);
  });

  it("is false when hidden and unfocused", () => {
    expect(isFocused("hidden", false)).toBe(false);
  });

  it("treats any non-visible visibilityState as away", () => {
    // "prerender" and the legacy "unloaded" are both real values.
    expect(isFocused("prerender", true)).toBe(false);
  });
});

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

describe("toParticipant", () => {
  const NOW = 1_700_000_000_000;
  const stamp = (ms: number) => ({ toMillis: () => ms });

  it("converts a server Timestamp to plain milliseconds", () => {
    const row = toParticipant("u1", { updatedAt: stamp(NOW - 1_000) }, false);
    expect(row.updatedAt).toBe(NOW - 1_000);
  });

  it("keeps a row alive while THIS browser's write is still in flight", () => {
    // serverTimestamp() reads back as null in the local snapshot Firestore
    // fires before the ack. Without this, your own avatar blinks out for the
    // round trip on every heartbeat, nickname change and focus change.
    const row = toParticipant("u1", { updatedAt: null }, true);
    expect(livingParticipants([row], Date.now())).toHaveLength(1);
  });

  it("still filters an unresolved timestamp that is NOT our pending write", () => {
    // The half that stops this becoming "NaN means fresh": only a document
    // this browser has an unacknowledged write on gets the benefit of the
    // doubt. Anything else with no usable timestamp is not a live row.
    const row = toParticipant("u1", { updatedAt: null }, false);
    expect(livingParticipants([row], Date.now())).toHaveLength(0);
  });

  it("prefers the acked server time over now, even while a write is pending", () => {
    // hasPendingWrites is a fallback for the unresolved field, not a licence
    // to overwrite a real timestamp with this browser's clock.
    const row = toParticipant("u1", { updatedAt: stamp(NOW - 5_000) }, true);
    expect(row.updatedAt).toBe(NOW - 5_000);
  });

  it("carries the document id and the published fields through", () => {
    const row = toParticipant(
      "u1",
      { name: "Ada", photoURL: "https://x/y", color: "#abc", focused: false },
      false,
    );
    expect(row).toMatchObject({
      uid: "u1",
      name: "Ada",
      photoURL: "https://x/y",
      color: "#abc",
      focused: false,
    });
  });

  it("defaults a row with no focus flag to focused rather than greying it out", () => {
    expect(toParticipant("u1", {}, false).focused).toBe(true);
  });

  it("defaults missing or wrongly typed fields rather than trusting them", () => {
    const row = toParticipant("u1", { name: 7, photoURL: 7, color: 7 }, false);
    expect(row).toMatchObject({ name: "", photoURL: null, color: "" });
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

describe("anchorFor", () => {
  function rect(left: number, width = 24, top = 40) {
    return { left, right: left + width, top, bottom: top + width };
  }

  it("grows rightwards from an avatar in the left half", () => {
    const a = anchorFor(rect(20), 1280, 800);
    expect(a.left).toBe(20);
    expect(a.right).toBeUndefined();
  });

  it("grows leftwards from an avatar in the right half", () => {
    const a = anchorFor(rect(1100), 1280, 800);
    expect(a.right).toBe(1280 - 1124);
    expect(a.left).toBeUndefined();
  });

  it("cannot overflow either edge at 375px, at any avatar position", () => {
    // The header already wraps at 375px, and a fixed tooltip poking past the
    // right edge would add horizontal scroll to the whole dashboard. Swept
    // rather than sampled, because the interesting case is the crossover in
    // the middle where the anchor flips edges.
    const vw = 375;
    for (let left = 0; left <= vw - 24; left += 1) {
      const a = anchorFor(rect(left), vw, 800);
      const start = a.left ?? vw - (a.right ?? 0) - a.maxWidth;
      const end = a.left !== undefined ? a.left + a.maxWidth : vw - (a.right ?? 0);
      expect(end).toBeLessThanOrEqual(vw);
      expect(start).toBeGreaterThanOrEqual(0);
    }
  });

  it("flips above the avatar when there is no room below", () => {
    const below = anchorFor(rect(20, 24, 40), 1280, 800);
    const above = anchorFor(rect(20, 24, 780), 1280, 800);
    expect(below.top).toBeGreaterThan(40);
    expect(above.top).toBeLessThan(780);
  });
});
