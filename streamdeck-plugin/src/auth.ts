import { ControlApiError } from "./api-client";

export type SignInResult = { token: string; discordUserId: string; discordUserName: string };

const POLL_MS = 2000;
const TIMEOUT_MS = 5 * 60 * 1000;

/** Start OAuth: opens the authorize URL via `openUrl`, then polls until the
 *  user completes sign-in in the browser. Resolves with the minted token and
 *  identity, or rejects with a ControlApiError (410 expired, 408 timeout). */
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
  if (!startRes.ok) throw new ControlApiError(startRes.status);
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
      if (Date.now() > deadline) throw new ControlApiError(408);
      continue;
    }
    if (!res.ok) throw new ControlApiError(res.status);
    const body = (await res.json()) as {
      token: string; discordUserId: string; discordUserName: string;
    };
    return { token: body.token, discordUserId: body.discordUserId,
             discordUserName: body.discordUserName };
  }
}
