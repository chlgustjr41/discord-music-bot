import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ControlApiError } from "../src/api-client";
import { SessionPoller, type PollState } from "../src/poller";

describe("SessionPoller", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function collect() {
    const states: PollState[] = [];
    return { states, cb: (s: PollState) => states.push(s) };
  }

  it("polls immediately on first subscribe and repeats at the base interval", async () => {
    const poll = vi.fn(async () => ({ active: false }) as const);
    const poller = new SessionPoller(poll, 5000, 30000);
    const { states, cb } = collect();
    poller.subscribe(cb);
    await vi.advanceTimersByTimeAsync(0);
    expect(poll).toHaveBeenCalledTimes(1);
    expect(states[0]).toEqual({ kind: "data", data: { active: false } });
    await vi.advanceTimersByTimeAsync(5000);
    expect(poll).toHaveBeenCalledTimes(2);
    poller.unsubscribe(cb);
  });

  it("stops polling when the last subscriber leaves", async () => {
    const poll = vi.fn(async () => ({ active: false }) as const);
    const poller = new SessionPoller(poll, 5000, 30000);
    const { cb } = collect();
    poller.subscribe(cb);
    await vi.advanceTimersByTimeAsync(0);
    poller.unsubscribe(cb);
    await vi.advanceTimersByTimeAsync(60000);
    expect(poll).toHaveBeenCalledTimes(1);
  });

  it("classifies failures and backs off after 3 consecutive, recovering on success", async () => {
    let failing = true;
    const poll = vi.fn(async () => {
      if (failing) throw new ControlApiError(500);
      return { active: false } as const;
    });
    const poller = new SessionPoller(poll, 5000, 30000);
    const { states, cb } = collect();
    poller.subscribe(cb);
    await vi.advanceTimersByTimeAsync(0);      // failure 1
    await vi.advanceTimersByTimeAsync(5000);   // failure 2
    await vi.advanceTimersByTimeAsync(5000);   // failure 3 -> backoff engaged
    expect(states.every((s) => s.kind === "offline")).toBe(true);
    await vi.advanceTimersByTimeAsync(5000);   // base interval: nothing (backing off)
    expect(poll).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(25000);  // 30s total since failure 3
    expect(poll).toHaveBeenCalledTimes(4);
    failing = false;
    await vi.advanceTimersByTimeAsync(30000);  // still backed off for this tick
    expect(states.at(-1)).toEqual({ kind: "data", data: { active: false } });
    await vi.advanceTimersByTimeAsync(5000);   // recovered -> base interval again
    expect(poll).toHaveBeenCalledTimes(6);
    poller.unsubscribe(cb);
  });

  it("does not stack chains when resubscribed while a poll is in flight", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    const poll = vi.fn(async () => {
      await gate;
      return { active: false } as const;
    });
    const poller = new SessionPoller(poll, 5000, 30000);
    const a = collect();
    const b = collect();
    poller.subscribe(a.cb);          // starts chain; poll 1 in flight, blocked
    poller.unsubscribe(a.cb);        // timer is null; nothing to clear
    poller.subscribe(b.cb);          // must NOT start a second chain
    release();
    await vi.advanceTimersByTimeAsync(0);
    expect(poll).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(5000);
    expect(poll).toHaveBeenCalledTimes(2);  // one chain, one poll per interval
    await vi.advanceTimersByTimeAsync(5000);
    expect(poll).toHaveBeenCalledTimes(3);
    poller.unsubscribe(b.cb);
  });

  it("keeps polling when a subscriber throws", async () => {
    const poll = vi.fn(async () => {
      throw new ControlApiError(500);
    });
    const poller = new SessionPoller(poll, 5000, 30000);
    const bad = vi.fn(() => { throw new Error("boom"); });
    const good = collect();
    poller.subscribe(bad);
    poller.subscribe(good.cb);
    await vi.advanceTimersByTimeAsync(0);
    expect(good.states[0]).toEqual({ kind: "offline" });
    await vi.advanceTimersByTimeAsync(5000);
    expect(poll).toHaveBeenCalledTimes(2);  // loop survived the throw
    poller.unsubscribe(bad);
    poller.unsubscribe(good.cb);
  });

  it("maps 401 to unauthorized and status 0 to unconfigured", async () => {
    const poller401 = new SessionPoller(async () => {
      throw new ControlApiError(401);
    }, 5000, 30000);
    const a = collect();
    poller401.subscribe(a.cb);
    await vi.advanceTimersByTimeAsync(0);
    expect(a.states[0]).toEqual({ kind: "unauthorized" });
    poller401.unsubscribe(a.cb);

    const poller0 = new SessionPoller(async () => {
      throw new ControlApiError(0);
    }, 5000, 30000);
    const b = collect();
    poller0.subscribe(b.cb);
    await vi.advanceTimersByTimeAsync(0);
    expect(b.states[0]).toEqual({ kind: "unconfigured" });
    poller0.unsubscribe(b.cb);
  });
});
