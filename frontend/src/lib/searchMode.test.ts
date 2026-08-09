import { describe, expect, it, vi } from "vitest";
import {
  nextOwnSearchState,
  runSearch,
  shouldAdoptSharedResults,
  shouldFollowSharedSearch,
  type OwnSearchState,
} from "./searchMode";

describe("shouldFollowSharedSearch", () => {
  it("follows in shared mode", () => {
    expect(shouldFollowSharedSearch("shared")).toBe(true);
  });

  it("ignores other people's searches in solo mode", () => {
    expect(shouldFollowSharedSearch("solo")).toBe(false);
  });
});

describe("runSearch", () => {
  it("uses the bot path in shared mode without touching the local endpoint", async () => {
    const local = vi.fn(async () => [{ videoId: "x" }]);
    const bot = vi.fn(async () => {});
    const out = await runSearch("shared", "hello", { local, bot });
    expect(bot).toHaveBeenCalledWith("hello");
    expect(local).not.toHaveBeenCalled();
    expect(out).toEqual({ via: "bot", results: null });
  });

  it("uses the local endpoint in solo mode", async () => {
    const local = vi.fn(async () => [{ videoId: "x" }]);
    const bot = vi.fn(async () => {});
    const out = await runSearch("solo", "hello", { local, bot });
    expect(local).toHaveBeenCalledWith("hello");
    expect(bot).not.toHaveBeenCalled();
    expect(out).toEqual({ via: "local", results: [{ videoId: "x" }] });
  });

  it("falls back to the bot when the local endpoint is unavailable", async () => {
    // True today: functions/searchYouTube is not deployed. Solo search must
    // still work rather than silently returning nothing.
    const local = vi.fn(async () => {
      throw new Error("404");
    });
    const bot = vi.fn(async () => {});
    const out = await runSearch("solo", "hello", { local, bot });
    expect(bot).toHaveBeenCalledWith("hello");
    expect(out).toEqual({ via: "bot-fallback", results: null });
  });

  it("propagates a bot failure rather than reporting success", async () => {
    const local = vi.fn(async () => {
      throw new Error("404");
    });
    const bot = vi.fn(async () => {
      throw new Error("firestore down");
    });
    // Specifically the BOT's error: asserting only "it threw" would also pass
    // if the fallback were never attempted and the local 404 leaked out.
    await expect(runSearch("solo", "hello", { local, bot })).rejects.toThrow(
      "firestore down",
    );
    expect(bot).toHaveBeenCalledWith("hello");
  });
});

describe("nextOwnSearchState", () => {
  const idle: OwnSearchState = { ownQuery: null, observed: false };

  it("records that the session is carrying our own query", () => {
    expect(nextOwnSearchState({ ownQuery: "radiohead", observed: false }, "radiohead")).toEqual({
      ownQuery: "radiohead",
      observed: true,
    });
  });

  it("drops our claim the moment somebody else's query overwrites the field", () => {
    expect(nextOwnSearchState({ ownQuery: "radiohead", observed: true }, "nickelback")).toEqual({
      ownQuery: null,
      observed: false,
    });
  });

  it("leaves the state alone when the bot has cleared the query", () => {
    const seen: OwnSearchState = { ownQuery: "radiohead", observed: true };
    expect(nextOwnSearchState(seen, "")).toEqual(seen);
    expect(nextOwnSearchState(seen, null)).toEqual(seen);
    expect(nextOwnSearchState(seen, undefined)).toEqual(seen);
  });

  it("stays idle while we have nothing in flight", () => {
    expect(nextOwnSearchState(idle, "nickelback")).toEqual(idle);
  });
});

describe("shouldAdoptSharedResults", () => {
  const solo = {
    mode: "solo" as const,
    waiting: true,
    ownQuery: "radiohead" as string | null,
    observedOwnQuery: true,
    incomingQuery: null as string | null | undefined,
  };

  it("adopts our own answer in solo mode once the bot clears the query", () => {
    expect(shouldAdoptSharedResults(solo)).toBe(true);
  });

  it("never adopts when this panel is not waiting for anything", () => {
    expect(shouldAdoptSharedResults({ ...solo, waiting: false })).toBe(false);
    expect(shouldAdoptSharedResults({ ...solo, mode: "shared", waiting: false })).toBe(false);
  });

  it("does not adopt while the bot is still processing", () => {
    expect(shouldAdoptSharedResults({ ...solo, incomingQuery: "radiohead" })).toBe(false);
    expect(shouldAdoptSharedResults({ ...solo, mode: "shared", incomingQuery: "x" })).toBe(false);
  });

  it("follows whatever the session searched in shared mode", () => {
    expect(
      shouldAdoptSharedResults({
        mode: "shared",
        waiting: true,
        ownQuery: null,
        observedOwnQuery: false,
        incomingQuery: null,
      }),
    ).toBe(true);
  });

  it("refuses solo results we never watched land in the shared field", () => {
    // The write may have failed, or a snapshot may have raced past it.
    expect(shouldAdoptSharedResults({ ...solo, observedOwnQuery: false })).toBe(false);
    expect(shouldAdoptSharedResults({ ...solo, ownQuery: null })).toBe(false);
  });

  it("solo Ada does not render shared Bob's results (the interleaving)", () => {
    // Ada is solo; searchYouTube 404s, so her search falls back to the bot.
    let ada: OwnSearchState = { ownQuery: "radiohead", observed: false };

    // Snapshot 1: her own write echoes back.
    ada = nextOwnSearchState(ada, "radiohead");
    expect(shouldAdoptSharedResults({ mode: "solo", waiting: true, ...state(ada), incomingQuery: "radiohead" })).toBe(false);

    // Snapshot 2: Bob (shared) searches "nickelback" and clobbers the field.
    ada = nextOwnSearchState(ada, "nickelback");

    // Snapshot 3: the bot answers BOB — query cleared, results present.
    expect(
      shouldAdoptSharedResults({ mode: "solo", waiting: true, ...state(ada), incomingQuery: null }),
    ).toBe(false);
  });

  it("still adopts when nobody interleaves", () => {
    let ada: OwnSearchState = { ownQuery: "radiohead", observed: false };
    ada = nextOwnSearchState(ada, "radiohead");
    expect(
      shouldAdoptSharedResults({ mode: "solo", waiting: true, ...state(ada), incomingQuery: null }),
    ).toBe(true);
  });
});

function state(s: OwnSearchState) {
  return { ownQuery: s.ownQuery, observedOwnQuery: s.observed };
}
