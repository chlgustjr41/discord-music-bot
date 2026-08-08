import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { resolveFfmpeg } from "./ffmpeg-path";

export const MAX_RECORD_MS = 15_000;

export function buildFfmpegArgs(device: string | undefined): string[] {
  return [
    "-hide_banner", "-loglevel", "error",
    "-f", "dshow", "-i", `audio=${device || "default"}`,
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

  start(device: string | undefined, onFirstBytes: () => void): boolean {
    const bin = resolveFfmpeg();
    if (!bin) return false;
    this.chunks = [];
    let first = true;
    this.proc = spawn(bin, buildFfmpegArgs(device));
    this.proc.stdout.on("data", (c: Buffer) => {
      if (first) { first = false; onFirstBytes(); }
      this.chunks.push(c);
    });
    this.proc.on("error", () => this.kill());
    this.timer = setTimeout(() => this.requestStop(), MAX_RECORD_MS);
    return true;
  }

  private requestStop(): void {
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
