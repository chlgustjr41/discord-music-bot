export type SignInResult = { token: string; discordUserId: string; discordUserName: string };

const POLL_MS = 2000;
const TIMEOUT_MS = 5 * 60 * 1000;

/** Why a sign-in ended without a token. The server's own error code is
 *  carried through so the Property Inspector can explain the cause rather
 *  than showing a bare status number. */
export class SignInError extends Error {
  constructor(
    readonly status: number,
    readonly code: string | null = null,
  ) {
    super(`sign-in failed (${status}${code ? `: ${code}` : ""})`);
    this.name = "SignInError";
  }
}

async function errorCodeOf(res: Response): Promise<string | null> {
  try {
    const body = (await res.json()) as { error?: string } | null;
    return body?.error ?? null;
  } catch {
    return null; // HTML page or empty body — status alone will have to do.
  }
}

/** Start OAuth: opens the authorize URL via `openUrl`, then polls until the
 *  user completes sign-in in the browser. Resolves with the minted token and
 *  identity, or rejects with a SignInError (410 expired, 408 timed out,
 *  403 not-a-member / device-mismatch). A network fault or the 10s per-request
 *  timeout rejects with the underlying fetch error instead. */
export async function signIn(
  apiUrl: string,
  openUrl: (url: string) => void,
  fetchFn: typeof fetch = fetch,
): Promise<SignInResult> {
  const base = apiUrl.replace(/\/+$/, "");
  const startRes = await fetchFn(`${base}/control/auth/start`, {
    method: "POST",
    signal: AbortSignal.timeout(10_000),
  });
  if (!startRes.ok) {
    throw new SignInError(startRes.status, await errorCodeOf(startRes));
  }
  const { state, authorizeUrl } = (await startRes.json()) as {
    state: string; authorizeUrl: string;
  };
  openUrl(authorizeUrl);
  const deadline = Date.now() + TIMEOUT_MS;
  for (;;) {
    await new Promise((r) => setTimeout(r, POLL_MS));
    const res = await fetchFn(
      `${base}/control/auth/poll?state=${encodeURIComponent(state)}`,
      { signal: AbortSignal.timeout(10_000) },
    );
    if (res.status === 202) {
      if (Date.now() > deadline) throw new SignInError(408, "timeout");
      continue;
    }
    if (!res.ok) throw new SignInError(res.status, await errorCodeOf(res));
    const body = (await res.json()) as {
      token: string; discordUserId: string; discordUserName: string;
    };
    return { token: body.token, discordUserId: body.discordUserId,
             discordUserName: body.discordUserName };
  }
}
