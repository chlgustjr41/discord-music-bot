/**
 * Which image URLs out of Firestore the dashboard is willing to load.
 *
 * Track thumbnails (queue, now playing, search results, playlists, history,
 * leaderboards) and the server icon are rendered as `<img src>` for everyone
 * looking at a session, and several of those fields can be written by any
 * visitor: a queue item is client-written, and so is a playlist or a
 * leaderboard row. An arbitrary URL there is a zero-interaction beacon — it
 * collects the IP address and User-Agent of every viewer, on every render.
 *
 * Firestore rules cannot close this for the queue (they cannot inspect the
 * items of a list), so the check lives where the request is actually issued:
 * the component. It is the artwork twin of `isAllowedPhotoUrl` in
 * lib/presence.ts, which does the same job for participant avatars.
 *
 * The allowlist is the set of hosts artwork legitimately comes from:
 *
 *   i.ytimg.com, img.youtube.com   YouTube thumbnails (Lavalink's artworkUrl,
 *                                  and the bot's own fallback URL)
 *   *.ggpht.com,
 *   *.googleusercontent.com        YouTube / YouTube Music channel and album art
 *   *.sndcdn.com (i1.sndcdn.com)   SoundCloud artwork
 *   cdn.discordapp.com             the Discord server icon
 *
 * Anything else — including artwork from a source not listed here — falls back
 * to the placeholder the component already has for "no thumbnail". Showing a
 * music-note icon for an unlisted host is the intended cost.
 */

/** Hosts matched exactly. */
const EXACT_HOSTS = new Set(["i.ytimg.com", "img.youtube.com", "cdn.discordapp.com"]);

/** Parent domains whose SUBDOMAINS are allowed (`*.example.com`). */
const WILDCARD_PARENTS = ["ggpht.com", "sndcdn.com", "googleusercontent.com"];

/** Longer than any real artwork URL; keeps a junk value from being parsed at all. */
const MAX_URL_LENGTH = 2048;

function isAllowedHost(hostname: string): boolean {
  const host = hostname.toLowerCase();
  if (EXACT_HOSTS.has(host)) return true;
  // Compared against the PARSED hostname with a leading dot, so neither
  // `evil-sndcdn.com` nor `sndcdn.com.evil.test` can match by substring.
  return WILDCARD_PARENTS.some((parent) => host.endsWith(`.${parent}`));
}

/**
 * The URL to put in `<img src>`, or `null` to show the placeholder instead.
 *
 * Returns the PARSED, normalised form rather than the input string, so the
 * value the browser requests is exactly the value that was checked — there is
 * no second parse of the raw text that could disagree with this one.
 */
export function safeArtworkUrl(url: unknown): string | null {
  if (typeof url !== "string" || url.length === 0 || url.length > MAX_URL_LENGTH) {
    return null;
  }
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  // https only: no http (mixed content, and a network observer), and none of
  // javascript: / data: / blob: / file:.
  if (parsed.protocol !== "https:") return null;
  // `https://i.ytimg.com@evil.test/` has the hostname evil.test and fails the
  // host check anyway; credentials on an ALLOWED host are still never
  // legitimate for artwork, so they are refused outright.
  if (parsed.username !== "" || parsed.password !== "") return null;
  // Artwork CDNs serve on 443. A different port is not something they use.
  if (parsed.port !== "") return null;
  if (!isAllowedHost(parsed.hostname)) return null;
  return parsed.href;
}

export function isAllowedArtworkUrl(url: unknown): boolean {
  return safeArtworkUrl(url) !== null;
}
