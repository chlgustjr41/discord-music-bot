import { describe, expect, it } from "vitest";
import {
  MAX_INPUT_LEN,
  mergeSections,
  shouldAdoptInput,
  shouldScheduleSearch,
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

describe("shouldScheduleSearch", () => {
  it("schedules a search for what this user typed", () => {
    expect(shouldScheduleSearch({ adopted: false, query: "radiohead" })).toBe(true);
  });

  it("refuses to search a value that arrived from someone else", () => {
    // The amplification case. Every viewer adopts Ada's query; if adoption
    // scheduled a search, every viewer would write it to servers/{id} and the
    // bot would answer the same question once per person watching.
    expect(shouldScheduleSearch({ adopted: true, query: "radiohead" })).toBe(false);
  });

  it("refuses regardless of how the adopted text looks", () => {
    // Pinned separately so that a guard which only special-cased, say, blank or
    // unchanged text would not pass by accident.
    for (const query of ["a", "radiohead in rainbows", "https://youtu.be/x", " padded "]) {
      expect(shouldScheduleSearch({ adopted: true, query })).toBe(false);
    }
  });

  it("does not search an empty or whitespace-only box", () => {
    expect(shouldScheduleSearch({ adopted: false, query: "" })).toBe(false);
    expect(shouldScheduleSearch({ adopted: false, query: "   " })).toBe(false);
  });
});

describe("MAX_INPUT_LEN", () => {
  it("is small enough to bound a write but large enough for a real query", () => {
    expect(MAX_INPUT_LEN).toBeGreaterThanOrEqual(100);
    expect(MAX_INPUT_LEN).toBeLessThanOrEqual(500);
  });
});
