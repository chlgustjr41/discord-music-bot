import { describe, expect, it } from "vitest";
import {
  MAX_INPUT_LEN,
  mergeSections,
  shouldAdoptInput,
  typistLabel,
  type InputEntry,
} from "./sharedView";
import type { Participant } from "./presence";

function entry(over: Partial<InputEntry> = {}): InputEntry {
  return { value: "radiohead", by: "ada", ...over };
}

function participant(over: Partial<Participant> = {}): Participant {
  return {
    uid: "ada", name: "Ada", photoURL: null,
    color: "hsl(1, 2%, 3%)", cursor: null, updatedAt: Date.now(), ...over,
  };
}

describe("mergeSections", () => {
  it("lets a remote value win over the panel default", () => {
    expect(mergeSections({ queue: false }, { queue: true, stats: false }))
      .toEqual({ queue: false, stats: false });
  });

  it("fills gaps with defaults so a panel nobody has touched still works", () => {
    expect(mergeSections({}, { queue: true })).toEqual({ queue: true });
  });

  it("ignores non-boolean remote values rather than rendering undefined", () => {
    expect(mergeSections({ queue: "yes" as unknown as boolean }, { queue: true }))
      .toEqual({ queue: true });
  });
});

describe("shouldAdoptInput", () => {
  const base = {
    focused: false, remoteBy: "ada", selfUid: "bob",
    remoteValue: "radiohead", localValue: "",
  };

  it("adopts someone else's value when you are not typing", () => {
    expect(shouldAdoptInput(base)).toBe(true);
  });

  it("never overwrites a field you are typing in", () => {
    // Two people typing at once each keep their own text until they blur,
    // rather than fighting mid-word.
    expect(shouldAdoptInput({ ...base, focused: true })).toBe(false);
  });

  it("ignores your own echo", () => {
    expect(shouldAdoptInput({ ...base, remoteBy: "bob" })).toBe(false);
  });

  it("does nothing when the value already matches", () => {
    expect(shouldAdoptInput({ ...base, localValue: "radiohead" })).toBe(false);
  });

  it("treats a missing writer as not adoptable", () => {
    expect(shouldAdoptInput({ ...base, remoteBy: "" })).toBe(false);
  });
});

describe("typistLabel", () => {
  const people = [participant()];

  it("names the person who last typed, with their presence colour", () => {
    expect(typistLabel(entry(), "bob", people)).toEqual({
      name: "Ada", color: "hsl(1, 2%, 3%)",
    });
  });

  it("is silent about your own typing", () => {
    expect(typistLabel(entry({ by: "bob" }), "bob", people)).toBeNull();
  });

  it("is silent when the writer has left the session", () => {
    // Their name would otherwise linger on a value nobody is still editing.
    expect(typistLabel(entry({ by: "ghost" }), "bob", people)).toBeNull();
  });

  it("is silent when there is no entry at all", () => {
    expect(typistLabel(undefined, "bob", people)).toBeNull();
  });
});

describe("MAX_INPUT_LEN", () => {
  it("is small enough to bound a write but large enough for a real query", () => {
    expect(MAX_INPUT_LEN).toBeGreaterThanOrEqual(100);
    expect(MAX_INPUT_LEN).toBeLessThanOrEqual(500);
  });
});
