/** Clickjacking defence that lives in the app instead of a response header.
 *
 *  A page that can be shown inside someone else's frame can be dressed up so
 *  that a visitor's clicks land on its real buttons: skip, end session,
 *  activate a server. The usual fix is an `X-Frame-Options` or
 *  `frame-ancestors` header, and that should still be added once it has been
 *  tested in a browser — but Google sign-in embeds a helper frame served from
 *  this same Hosting site, and a header that reaches it breaks login.
 *
 *  Refusing to render when embedded has no such risk, and for an app that is
 *  drawn entirely by script it holds up: if the framing page lets scripts
 *  run, this check runs; if it disables them (a sandboxed frame), nothing is
 *  drawn at all, so there is nothing to click.
 */

/** True when this window is not the top-level one. If the browser refuses to
 *  say, that refusal is itself the answer. */
export function isEmbedded(win: Window = window): boolean {
  try {
    return win.top !== win.self;
  } catch {
    return true;
  }
}

/** Development is exempt: editor and preview panes embed the dev server. */
export function refusesToRender(win: Window = window, isDev: boolean = import.meta.env.DEV): boolean {
  return !isDev && isEmbedded(win);
}
