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
