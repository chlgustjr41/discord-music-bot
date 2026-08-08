/** Fetch track artwork and encode it for `setImage`.
 *
 *  Bounded on purpose: a Stream Deck key is 72px, so anything past the
 *  ceiling is a wrong URL rather than album art. Every failure resolves to
 *  null so the caller can fall back to the default icon — artwork is never
 *  worth breaking the key over.
 */

const MAX_BYTES = 2 * 1024 * 1024;
const TIMEOUT_MS = 5000;

export async function loadThumbnail(
  url: string,
  fetchFn: typeof fetch = fetch,
): Promise<string | null> {
  try {
    const res = await fetchFn(url, { signal: AbortSignal.timeout(TIMEOUT_MS) });
    if (!res.ok) return null;
    const type = res.headers.get("content-type") ?? "image/jpeg";
    if (!type.startsWith("image/")) return null;
    const buf = await res.arrayBuffer();
    if (buf.byteLength === 0 || buf.byteLength > MAX_BYTES) return null;
    return `data:${type.split(";")[0]};base64,${Buffer.from(buf).toString("base64")}`;
  } catch {
    return null;
  }
}
