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
  /** True iff a tick chain is alive (poll in flight or timer pending).
   *  Guards against starting a second chain when a resubscribe happens
   *  while a poll is still in flight (timer is null at that moment). */
  private polling = false;
  // Deliberately persists across unsubscribe/resubscribe: the server was
  // just failing, so the backoff state is still real.
  private failures = 0;

  constructor(
    private readonly poll: () => Promise<NowPlaying>,
    private readonly baseMs = 5000,
    private readonly maxMs = 30000,
  ) {}

  subscribe(cb: (s: PollState) => void): void {
    this.subs.add(cb);
    if (this.subs.size === 1 && !this.polling) {
      this.polling = true;
      void this.tick();
    }
  }

  unsubscribe(cb: (s: PollState) => void): void {
    this.subs.delete(cb);
    if (this.subs.size === 0 && this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
      this.polling = false;
    }
  }

  private emit(s: PollState): void {
    // Set iteration is safe against self-unsubscribe; per-callback try/catch
    // keeps one bad subscriber from killing the shared poll loop.
    for (const cb of this.subs) {
      try {
        cb(s);
      } catch {
        // subscriber errors must not break polling
      }
    }
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
    } else {
      this.polling = false;
    }
  }
}
