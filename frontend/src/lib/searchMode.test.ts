import { describe, expect, it, vi } from "vitest";
import { runSearch, shouldFollowSharedSearch } from "./searchMode";

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
