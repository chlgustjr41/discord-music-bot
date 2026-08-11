import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  execFile: vi.fn(),
  resolve: vi.fn<() => string | null>(() => "ffmpeg"),
}));

vi.mock("node:child_process", () => ({
  execFile: (...args: unknown[]) => h.execFile(...args),
}));
vi.mock("../src/ffmpeg-path", () => ({ resolveFfmpeg: () => h.resolve() }));

const { listAudioDevices, resolveInputDevice, forgetResolvedDevice } = await import(
  "../src/audio-devices"
);

/** ffmpeg prints the device list to stderr and exits non-zero by design. */
function devicesOnStderr(...names: string[]): void {
  const stderr = names.map((n) => `[dshow @ 0x1] "${n}" (audio)\n`).join("");
  h.execFile.mockImplementation((...args: unknown[]) => {
    const cb = args[2] as (e: unknown, o: string, s: string) => void;
    cb(new Error("exit 1"), "", stderr);
  });
}

beforeEach(() => {
  h.execFile.mockReset();
  h.resolve.mockReset().mockReturnValue("ffmpeg");
  forgetResolvedDevice();
});

describe("listAudioDevices", () => {
  it("reads the audio device names out of ffmpeg's stderr", async () => {
    devicesOnStderr("Microphone (3- Logitech G733 Gaming Headset)", "Microphone (Yeti GX)");
    expect(await listAudioDevices()).toEqual([
      "Microphone (3- Logitech G733 Gaming Headset)",
      "Microphone (Yeti GX)",
    ]);
  });

  it("returns nothing when there is no ffmpeg to ask", async () => {
    h.resolve.mockReturnValue(null);
    expect(await listAudioDevices()).toEqual([]);
    expect(h.execFile).not.toHaveBeenCalled();
  });
});

describe("resolveInputDevice", () => {
  it("uses the key's configured device without enumerating anything", async () => {
    devicesOnStderr("Microphone (Yeti GX)");
    expect(await resolveInputDevice("Microphone (3- Logitech G733 Gaming Headset)")).toBe(
      "Microphone (3- Logitech G733 Gaming Headset)",
    );
    // Enumeration spawns ffmpeg and costs seconds — never pay it for a key
    // that already knows which microphone it wants.
    expect(h.execFile).not.toHaveBeenCalled();
  });

  it("auto-picks the first enumerated device when the key has none", async () => {
    devicesOnStderr("Microphone (3- Logitech G733 Gaming Headset)", "Microphone (Yeti GX)");
    expect(await resolveInputDevice(undefined)).toBe(
      "Microphone (3- Logitech G733 Gaming Headset)",
    );
  });

  it("treats a blank stored device as no device at all", async () => {
    devicesOnStderr("Microphone (Yeti GX)");
    expect(await resolveInputDevice("   ")).toBe("Microphone (Yeti GX)");
  });

  it("enumerates once and reuses the answer for every later press", async () => {
    // DirectShow already costs ~1-1.8 s to open; a multi-second enumeration on
    // top of that, on every press, would make the key unusable.
    devicesOnStderr("Microphone (Yeti GX)");
    await resolveInputDevice(undefined);
    await resolveInputDevice(undefined);
    await resolveInputDevice(undefined);
    expect(h.execFile).toHaveBeenCalledTimes(1);
  });

  it("reports no device rather than inventing one, and retries next press", async () => {
    // Nothing is cached on failure: a microphone plugged in after the first
    // press must not need a plugin restart to be found.
    devicesOnStderr();
    expect(await resolveInputDevice(undefined)).toBeNull();
    devicesOnStderr("Microphone (Yeti GX)");
    expect(await resolveInputDevice(undefined)).toBe("Microphone (Yeti GX)");
    expect(h.execFile).toHaveBeenCalledTimes(2);
  });
});
