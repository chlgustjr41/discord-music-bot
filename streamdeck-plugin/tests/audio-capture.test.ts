import { EventEmitter } from "node:events";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const spawnMock = vi.fn();
const resolveMock = vi.fn<() => string | null>(() => "ffmpeg");

vi.mock("node:child_process", () => ({
  spawn: (...args: unknown[]) => spawnMock(...args),
}));
vi.mock("../src/ffmpeg-path", () => ({ resolveFfmpeg: () => resolveMock() }));

const { buildFfmpegArgs, MicRecorder, MAX_RECORD_MS } = await import("../src/audio-capture");

/** Minimal stand-in for a spawned ffmpeg: stdout emits audio, stdin takes the
 *  graceful-quit "q", and "close"/"error" drive the recorder's state machine. */
class FakeProc extends EventEmitter {
  stdout = new EventEmitter();
  stdin = { write: vi.fn() };
  kill = vi.fn();
}

let proc: FakeProc;

beforeEach(() => {
  proc = new FakeProc();
  spawnMock.mockReset().mockReturnValue(proc);
  resolveMock.mockReset().mockReturnValue("ffmpeg");
});

afterEach(() => {
  vi.useRealTimers();
});

describe("buildFfmpegArgs", () => {
  it("captures the named device as 16 kHz mono WAV on stdout", () => {
    const args = buildFfmpegArgs("Microphone (Yeti GX)");
    expect(args).toContain("dshow");
    expect(args).toContain("audio=Microphone (Yeti GX)");
    expect(args.join(" ")).toContain("-ar 16000");
    expect(args.join(" ")).toContain("-ac 1");
    expect(args[args.length - 1]).toBe("pipe:1");
  });

  it("falls back to the system default when no device is configured", () => {
    // ffmpeg's dshow needs a name; "default" is the documented placeholder.
    expect(buildFfmpegArgs("").join(" ")).toContain("audio=default");
    expect(buildFfmpegArgs(undefined).join(" ")).toContain("audio=default");
  });
});

describe("MicRecorder", () => {
  it("spawns the resolved binary with the built arguments", () => {
    const rec = new MicRecorder();
    expect(rec.start("Yeti", () => {})).toBe(true);
    expect(spawnMock).toHaveBeenCalledWith("ffmpeg", buildFfmpegArgs("Yeti"));
    expect(rec.spawnFailed).toBe(false);
  });

  it("refuses to start when no ffmpeg can be resolved", () => {
    resolveMock.mockReturnValue(null);
    const rec = new MicRecorder();
    expect(rec.start("Yeti", () => {})).toBe(false);
    expect(spawnMock).not.toHaveBeenCalled();
  });

  it("fires onFirstBytes once, when audio actually starts flowing", () => {
    // DirectShow takes ~1-1.8 s to open, so this — not key-down — is when the
    // key may claim to be listening.
    const onFirst = vi.fn();
    new MicRecorder().start("Yeti", onFirst);
    expect(onFirst).not.toHaveBeenCalled();
    proc.stdout.emit("data", Buffer.from("aa"));
    proc.stdout.emit("data", Buffer.from("bb"));
    expect(onFirst).toHaveBeenCalledTimes(1);
  });

  it("stops gracefully and resolves with everything captured", async () => {
    const rec = new MicRecorder();
    rec.start("Yeti", () => {});
    proc.stdout.emit("data", Buffer.from("RIFF"));
    proc.stdout.emit("data", Buffer.from("data"));

    const stopped = rec.stop();
    expect(proc.stdin.write).toHaveBeenCalledWith("q");
    proc.emit("close");
    expect((await stopped).toString()).toBe("RIFFdata");
  });

  it("caps an unreleased key at 15 s instead of recording forever", () => {
    vi.useFakeTimers();
    new MicRecorder().start("Yeti", () => {});
    vi.advanceTimersByTime(MAX_RECORD_MS - 1);
    expect(proc.stdin.write).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(proc.stdin.write).toHaveBeenCalledWith("q");
  });

  it("returns empty audio for a hold too short to produce any", async () => {
    const rec = new MicRecorder();
    rec.start("Yeti", () => {});
    const stopped = rec.stop();
    proc.emit("close");
    const wav = await stopped;
    expect(wav.length).toBe(0); // the key's "Hold longer" branch
    expect(rec.spawnFailed).toBe(false);
  });

  it("reports a spawn failure distinctly from a silent recording", async () => {
    // ENOENT: ffmpeg is in neither the bundle nor PATH. Both cases yield zero
    // bytes, so without this flag the key would wrongly say "Hold longer".
    const rec = new MicRecorder();
    rec.start("Yeti", () => {});
    proc.emit("error", new Error("spawn ffmpeg ENOENT"));
    expect(rec.spawnFailed).toBe(true);
    expect(proc.kill).toHaveBeenCalled();
    // Resolves without waiting on a "close" that will never come.
    expect((await rec.stop()).length).toBe(0);
  });

  it("stop() is safe when nothing was ever started", async () => {
    expect((await new MicRecorder().stop()).length).toBe(0);
  });
});
