/** Fetch track artwork and encode it for `setImage`.
 *
 *  Bounded on purpose: a Stream Deck key is 72px, so anything past the
 *  ceiling is a wrong URL rather than album art. Every failure resolves to
 *  null so the caller can fall back to the default icon — artwork is never
 *  worth breaking the key over.
 */

const MAX_BYTES = 2 * 1024 * 1024;
const TIMEOUT_MS = 5000;

/** The practical ceiling on the encoded string, as opposed to MAX_BYTES, which
 *  is the outer bound on what an unknown host may hand us.
 *
 *  The reported bug is this number: a 1280x720 `maxresdefault.jpg` base64'd
 *  into an SVG came to 258,023 characters, went over the Stream Deck websocket
 *  to a 72-pixel key, and rendered nothing. `mqdefault.jpg` (320x180) is ~10 KB
 *  on the wire and ~14 KB encoded, so 64 KB leaves roughly 4x headroom for an
 *  unusually detailed frame or a non-YouTube host serving a moderate cover,
 *  while staying 4x under the payload that demonstrably failed. Past it the
 *  glyph is worth more than the artwork. */
export const MAX_ENCODED_CHARS = 64 * 1024;

/** 320x180 — still four times a 72-pixel key's resolution. */
const SMALL_VARIANT = "mqdefault";

/** YouTube's fixed thumbnail vocabulary. Only these are rewritten; an
 *  unrecognised basename is left alone rather than guessed at. */
const KNOWN_VARIANTS = new Set([
  "default",
  "mqdefault",
  "hqdefault",
  "sddefault",
  "maxresdefault",
]);

const YT_THUMB_PATH = /^\/(vi|vi_webp)\/([^/]+)\/([^/]+)\.(jpg|webp)$/;

/** Rewrite a known YouTube thumbnail URL down to a size a key can render.
 *
 *  Pure and exported so the choice of variant is testable without touching the
 *  network. Anything not recognised — another host, another path shape, not a
 *  URL at all — comes back untouched: an unknown host has no variant
 *  vocabulary to rewrite into, and the size cap is what makes leaving it alone
 *  safe. */
export function smallThumbnailUrl(raw: string): string {
  try {
    const u = new URL(raw);
    if (!/(^|\.)ytimg\.com$/.test(u.hostname)) return raw;
    const m = YT_THUMB_PATH.exec(u.pathname);
    if (!m || !KNOWN_VARIANTS.has(m[3])) return raw;
    u.pathname = `/${m[1]}/${m[2]}/${SMALL_VARIANT}.${m[4]}`;
    return u.toString();
  } catch {
    return raw;
  }
}

export async function loadThumbnail(
  url: string,
  fetchFn: typeof fetch = fetch,
): Promise<string | null> {
  try {
    // Rewritten BEFORE the request, not after: the point is never to pull
    // 1280x720 down a link in the first place.
    const target = smallThumbnailUrl(url);
    const res = await fetchFn(target, { signal: AbortSignal.timeout(TIMEOUT_MS) });
    if (!res.ok) return null;
    const type = res.headers.get("content-type") ?? "image/jpeg";
    if (!type.startsWith("image/")) return null;
    const declared = Number(res.headers.get("content-length"));
    // Check the declared size BEFORE buffering: arrayBuffer() would otherwise
    // materialize the whole body in memory and only then reject it. Servers
    // that omit content-length still fall through to the post-read check.
    if (Number.isFinite(declared) && declared > MAX_BYTES) return null;
    const buf = await res.arrayBuffer();
    if (buf.byteLength === 0 || buf.byteLength > MAX_BYTES) return null;
    return `data:${type.split(";")[0]};base64,${Buffer.from(buf).toString("base64")}`;
  } catch {
    return null;
  }
}
