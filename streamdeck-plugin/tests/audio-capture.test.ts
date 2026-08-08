import { describe, expect, it } from "vitest";
import { buildFfmpegArgs } from "../src/audio-capture";

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
