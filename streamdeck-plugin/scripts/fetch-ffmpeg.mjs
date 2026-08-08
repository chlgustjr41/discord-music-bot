/**
 * Download the pinned ffmpeg into the plugin bundle so the packaged plugin
 * needs no user setup. Runs on the DEV machine before `pack`, never at
 * runtime. The SHA-256 is pinned: a swapped or corrupted upstream artifact
 * fails the build instead of shipping silently.
 *
 * Pin: BtbN/FFmpeg-Builds release autobuild-2026-08-06-13-39, LGPL win64.
 * LGPL (not GPL) because the binary is redistributed inside the plugin.
 */
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, copyFileSync, writeFileSync, readdirSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const URL_ =
  "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-08-06-13-39/ffmpeg-N-125978-g95c43d7df7-win64-lgpl.zip";
const SHA256 = "79ab2838ff13a71df85ba452d633b964fe5cc681f7eccb1f3e873649974fbe1f";
const OUT_DIR = path.resolve("com.jacobchoi.jacky-control.sdPlugin/bin");

if (existsSync(path.join(OUT_DIR, "ffmpeg.exe"))) {
  console.log("ffmpeg already bundled — nothing to do");
  process.exit(0);
}

const res = await fetch(URL_);
if (!res.ok) throw new Error(`download failed: ${res.status}`);
const buf = Buffer.from(await res.arrayBuffer());

const got = createHash("sha256").update(buf).digest("hex");
if (got !== SHA256) throw new Error(`SHA-256 mismatch: expected ${SHA256}, got ${got}`);

const tmp = mkdtempSync(path.join(tmpdir(), "ffmpeg-"));
const zip = path.join(tmp, "ffmpeg.zip");
writeFileSync(zip, buf);
// PowerShell rather than a zip dependency: this runs only on the dev machine.
execFileSync("powershell", ["-NoProfile", "-Command",
  `Expand-Archive -Path '${zip}' -DestinationPath '${tmp}' -Force`]);

const root = readdirSync(tmp).find((d) => d.startsWith("ffmpeg-"));
const exe = path.join(tmp, root, "bin", "ffmpeg.exe");
mkdirSync(OUT_DIR, { recursive: true });
copyFileSync(exe, path.join(OUT_DIR, "ffmpeg.exe"));
console.log("bundled ffmpeg ->", path.join(OUT_DIR, "ffmpeg.exe"));
