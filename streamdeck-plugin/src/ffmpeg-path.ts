import { existsSync } from "node:fs";
import path from "node:path";

/** Bundled binary first so the plugin works with nothing installed; PATH is
 *  the fallback for dev machines. null means "tell the user". */
export function resolveFfmpeg(): string | null {
  const bundled = path.resolve(process.cwd(), "bin", "ffmpeg.exe");
  if (existsSync(bundled)) return bundled;
  return process.env.PATH ? "ffmpeg" : null;
}
