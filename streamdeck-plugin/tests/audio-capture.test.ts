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
  stderr = new EventEmitter();
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

  it("never emits audio=default — no such DirectShow device exists", () => {
    // THE regression. `ffmpeg -f dshow -i "audio=default"` answers
    // "Could not find audio only device with name [default]" and exits having
    // written nothing, which the key could not tell apart from a short press.
    // dshow has no placeholder name: it wants a device that really exists.
    expect(buildFfmpegArgs("Microphone (Yeti GX)").join(" ")).not.toContain("audio=default");
    for (const nothing of ["", "   ", undefined, null]) {
      expect(() => buildFfmpegArgs(nothing as unknown as string)).toThrow();
    }
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
    // Timing is the assertion: under fake timers, with no "close" emitted and
    // no timer advanced, this only settles because stop() returns early. If it
    // waited on the 2 s fallback the await would never resolve.
    vi.useFakeTimers();
    expect((await rec.stop()).length).toBe(0);
  });

  it("stop() is safe when nothing was ever started", async () => {
    expect((await new MicRecorder().stop()).length).toBe(0);
  });

  it("reports a capture that died on its own distinctly from a short press", async () => {
    // The invisible failure: ffmpeg SPAWNS fine, then exits immediately because
    // the device does not exist. Zero bytes either way, so only "it exited
    // non-zero before anyone asked it to stop" separates the two.
    const rec = new MicRecorder();
    rec.start("Nope", () => {});
    proc.stderr.emit("data", Buffer.from("Could not find audio only device"));
    proc.emit("close", 1);

    expect(rec.micFailed).toBe(true);
    expect(rec.spawnFailed).toBe(false);
    expect(rec.exitCode).toBe(1);
    expect(rec.stderr).toContain("Could not find audio only device");
    // And it must not hang: the process is already gone, so there is no second
    // "close" coming for stop() to wait on.
    vi.useFakeTimers();
    expect((await rec.stop()).length).toBe(0);
  });

  it("does not call a graceful quit a mic failure", async () => {
    // ffmpeg answers "q" with a non-zero status in some builds. Asking it to
    // stop and then seeing it stop is not a fault, however it words the exit.
    const rec = new MicRecorder();
    rec.start("Yeti", () => {});
    const stopped = rec.stop();
    proc.emit("close", 255);
    await stopped;
    expect(rec.micFailed).toBe(false);
  });

  it("does not call a clean exit a mic failure", async () => {
    const rec = new MicRecorder();
    rec.start("Yeti", () => {});
    proc.emit("close", 0);
    expect(rec.micFailed).toBe(false);
  });
});
