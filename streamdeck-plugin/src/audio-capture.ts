import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { resolveFfmpeg } from "./ffmpeg-path";

export const MAX_RECORD_MS = 15_000;

/** How much of ffmpeg's stderr to keep. Enough for the device error and the
 *  couple of lines around it; a capture that runs its full 15 s must not grow
 *  a string without bound. */
const MAX_STDERR_CHARS = 2000;

/** DirectShow requires a device that really exists — there is no placeholder
 *  name. This used to fall back to `audio=default`, which ffmpeg rejects with
 *  "Could not find audio only device with name [default]" before writing a
 *  single byte. Throwing makes that impossible to reintroduce by accident:
 *  the caller must resolve a real device (see resolveInputDevice) first. */
export function buildFfmpegArgs(device: string): string[] {
  const name = String(device ?? "").trim();
  if (!name) {
    throw new Error("buildFfmpegArgs needs a real DirectShow device name");
  }
  return [
    "-hide_banner", "-loglevel", "error",
    "-f", "dshow", "-i", `audio=${name}`,
    "-ac", "1", "-ar", "16000",
    "-f", "wav", "pipe:1",
  ];
}

/** Push-to-talk recorder. `onFirstBytes` fires when audio actually starts
 *  flowing — DirectShow takes ~1-1.8 s to open a device, and the key uses
 *  this to show "Listening…" so the user knows when to speak. */
export class MicRecorder {
  private proc: ChildProcessWithoutNullStreams | null = null;
  private chunks: Buffer[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private failed = false;
  private died = false;
  private closed = false;
  private stopRequested = false;
  private code: number | null = null;
  private stderrText = "";

  /** True when the process never launched (ffmpeg missing, ENOENT). The key
   *  uses this to say "No ffmpeg" instead of "Hold longer" — both otherwise
   *  look identical from here: zero captured bytes. */
  get spawnFailed(): boolean {
    return this.failed;
  }

  /** True when ffmpeg launched and then died on its own — it exited non-zero
   *  before anyone asked it to stop. That is the shape of a device that does
   *  not exist or cannot be opened, and it is the third outcome this class
   *  used to collapse into "Hold longer": a spawn that succeeds and a capture
   *  that works both end in zero bytes when the device is wrong.
   *
   *  "Before anyone asked it to stop" is the discriminator rather than the
   *  exit code alone, because ffmpeg answers the graceful "q" with a non-zero
   *  status in some builds — a press released early would otherwise be
   *  reported as a hardware fault. */
  get micFailed(): boolean {
    return this.died;
  }

  get exitCode(): number | null {
    return this.code;
  }

  /** ffmpeg's own account of why it stopped. Logged on a mic error so the
   *  reason reaches the plugin log instead of dying with the process. */
  get stderr(): string {
    return this.stderrText;
  }

  start(device: string, onFirstBytes: () => void): boolean {
    const bin = resolveFfmpeg();
    if (!bin) return false;
    this.chunks = [];
    this.failed = false;
    this.died = false;
    this.closed = false;
    this.stopRequested = false;
    this.code = null;
    this.stderrText = "";
    let first = true;
    this.proc = spawn(bin, buildFfmpegArgs(device));
    this.proc.stdout.on("data", (c: Buffer) => {
      if (first) { first = false; onFirstBytes(); }
      this.chunks.push(c);
    });
    this.proc.stderr.on("data", (c: Buffer) => {
      if (this.stderrText.length < MAX_STDERR_CHARS) {
        this.stderrText = (this.stderrText + String(c)).slice(0, MAX_STDERR_CHARS);
      }
    });
    // Registered before stop()'s own listener, so this always records the
    // outcome first — and it must survive a close that arrives long before
    // any key-up, which is exactly what the dead-device case does.
    this.proc.on("close", (code: number | null) => {
      this.closed = true;
      this.code = code;
      if (!this.stopRequested && code !== 0) this.died = true;
    });
    this.proc.on("error", () => {
      this.failed = true;
      this.kill();
    });
    this.timer = setTimeout(() => this.requestStop(), MAX_RECORD_MS);
    return true;
  }

  private requestStop(): void {
    this.stopRequested = true;
    // "q" is ffmpeg's graceful quit: it finalizes the WAV header.
    try { this.proc?.stdin.write("q"); } catch { /* already gone */ }
  }

  private kill(): void {
    try { this.proc?.kill(); } catch { /* already gone */ }
  }

  async stop(): Promise<Buffer> {
    if (this.timer) { clearTimeout(this.timer); this.timer = null; }
    const proc = this.proc;
    if (!proc) return Buffer.alloc(0);
    // Nothing to wait for: the process never started, or it is already gone —
    // a dead capture emitted its "close" before the key was even released, so
    // waiting on another one would just burn the 2 s fallback.
    if (this.failed || this.closed) {
      this.proc = null;
      return Buffer.concat(this.chunks);
    }
    this.requestStop();
    await new Promise<void>((resolve) => {
      const done = () => resolve();
      proc.once("close", done);
      // Don't hang the key if ffmpeg ignores the quit.
      setTimeout(() => { this.kill(); done(); }, 2000);
    });
    this.proc = null;
    return Buffer.concat(this.chunks);
  }
}
