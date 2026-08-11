import { execFile } from "node:child_process";
import { resolveFfmpeg } from "./ffmpeg-path";

/** ffmpeg prints the device list to stderr and exits non-zero by design. */
export function listAudioDevices(): Promise<string[]> {
  return new Promise((resolve) => {
    const bin = resolveFfmpeg();
    if (!bin) return resolve([]);
    execFile(
      bin,
      ["-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
      (_err, _stdout, stderr) => {
        const names = [...String(stderr).matchAll(/"([^"]+)"\s*\(audio\)/g)].map(
          (m) => m[1],
        );
        resolve([...new Set(names)]);
      },
    );
  });
}

/** The device auto-picked for keys that have none configured.
 *
 *  Cached at module scope, deliberately: enumeration spawns ffmpeg and costs
 *  seconds, and DirectShow already takes ~1-1.8 s to open the device once the
 *  key is held. Paying for a device list on every press would put that in
 *  front of every spoken command. Every voice key shares the pick because the
 *  device list is a property of the machine, not of a key.
 *
 *  A FAILED lookup is not cached: a microphone plugged in after the first
 *  press must not need a plugin restart to be found, and the retry only ever
 *  happens on a path that is already broken. */
let autoPicked: string | null = null;

/** The device to record from, or null when the machine has no audio input.
 *
 *  There is no fallback name to invent. `audio=default` is not a DirectShow
 *  device — ffmpeg answers "Could not find audio only device with name
 *  [default]" and exits having written nothing, which the key could not tell
 *  apart from a press too short to capture anything. */
export async function resolveInputDevice(configured?: string): Promise<string | null> {
  const named = configured?.trim();
  if (named) return named;
  if (autoPicked) return autoPicked;
  autoPicked = (await listAudioDevices())[0] ?? null;
  return autoPicked;
}

/** Drop the cached pick so the next unconfigured press enumerates again. */
export function forgetResolvedDevice(): void {
  autoPicked = null;
}
