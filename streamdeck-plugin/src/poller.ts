import { ControlApiError, type NowPlaying } from "./api-client";

export type PollState =
  | { kind: "data"; data: NowPlaying }
  | { kind: "offline" }
  | { kind: "unauthorized" }
  | { kind: "unconfigured" };

const BACKOFF_AFTER_FAILURES = 3;

/** Single shared now-playing poll loop. Runs only while subscribed; backs
 *  off from baseMs to maxMs after consecutive failures. */
export class SessionPoller {
  private readonly subs = new Set<(s: PollState) => void>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private failures = 0;

  constructor(
    private readonly poll: () => Promise<NowPlaying>,
    private readonly baseMs = 5000,
    private readonly maxMs = 30000,
  ) {}

  subscribe(cb: (s: PollState) => void): void {
    this.subs.add(cb);
    if (this.subs.size === 1) void this.tick();
  }

  unsubscribe(cb: (s: PollState) => void): void {
    this.subs.delete(cb);
    if (this.subs.size === 0 && this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  private emit(s: PollState): void {
    for (const cb of this.subs) cb(s);
  }

  private async tick(): Promise<void> {
    this.timer = null;
    try {
      const data = await this.poll();
      this.failures = 0;
      this.emit({ kind: "data", data });
    } catch (err) {
      this.failures += 1;
      if (err instanceof ControlApiError && err.status === 401) {
        this.emit({ kind: "unauthorized" });
      } else if (err instanceof ControlApiError && err.status === 0) {
        this.emit({ kind: "unconfigured" });
      } else {
        this.emit({ kind: "offline" });
      }
    }
    if (this.subs.size > 0) {
      const delay = this.failures >= BACKOFF_AFTER_FAILURES ? this.maxMs : this.baseMs;
      this.timer = setTimeout(() => void this.tick(), delay);
    }
  }
}
