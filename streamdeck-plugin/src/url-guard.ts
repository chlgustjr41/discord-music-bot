/**
 * Whether a URL is safe to hand to streamDeck.system.openUrl.
 *
 * The server supplies these URLs — both in a voice `client` directive and in
 * the Dashboard key's response — and `apiUrl` is user-overridable, so the
 * responding server is not guaranteed to be the real one.
 *
 * Origin pinning is deliberately NOT attempted: the plugin has no trusted
 * web-app origin to pin against (only DEFAULT_API_URL, which the user can
 * override), so any such check would be theatre. What this DOES stop is the
 * real escalation — javascript:, file:, data: and custom-scheme URLs that
 * execute locally or hand off to another installed application, rather than
 * merely navigating to an unexpected page.
 *
 * The hostname check is belt-and-braces: Node's WHATWG parser already throws
 * on "https://" (no host), but a special-scheme URL with an empty host must
 * never reach openUrl if some other runtime parses it.
 */
export function isOpenableUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && parsed.hostname.length > 0;
  } catch {
    return false;
  }
}
