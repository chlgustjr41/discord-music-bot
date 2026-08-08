import streamDeck from "@elgato/streamdeck";
import { ControlApiError, JackyClient } from "./api-client";
import { signIn, SignInError } from "./auth";
import { SessionPoller } from "./poller";
import { effectiveApiUrl, settingsReady, type GlobalSettings } from "./settings";

let client: JackyClient | null = null;

export function getClient(): JackyClient | null {
  return client;
}

export const poller = new SessionPoller(async () => {
  if (!client) throw new ControlApiError(0); // -> "unconfigured"
  return client.nowPlaying();
});

/** Rebuild the client from settings and re-poll. */
function apply(s: GlobalSettings): void {
  client = settingsReady(s)
    ? new JackyClient({ apiUrl: effectiveApiUrl(s), authToken: s.authToken })
    : null;
  poller.kick();
}

/** Load global settings and rebuild the client whenever they change. */
export async function initRuntime(): Promise<void> {
  streamDeck.settings.onDidReceiveGlobalSettings<GlobalSettings>((ev) =>
    apply(ev.settings),
  );
  apply(await streamDeck.settings.getGlobalSettings<GlobalSettings>());
}

export type SignInFlowResult = { ok: boolean; userName?: string; error?: string };

/** Turn a failed sign-in into something a person can act on. The server's
 *  error code is more specific than the status (403 covers both "you're not
 *  in a server Jacky serves" and "this sign-in was started elsewhere"). */
function describeSignInFailure(err: unknown): string {
  if (err instanceof SignInError) {
    switch (err.code) {
      case "not-a-member":
        return "Your Discord account isn't in a server Jacky Music serves.";
      case "device-mismatch":
        return "Blocked: finish sign-in in the browser on this computer — never from a link someone sent you.";
      case "timeout":
        return "Sign-in timed out. Try again.";
      case "unknown-state":
        return "Sign-in expired. Try again.";
      case "busy":
      case "rate-limited":
        return "The server is busy with sign-ins. Try again in a minute.";
      default:
        return `Sign-in failed (${err.status}).`;
    }
  }
  if (err instanceof Error && err.name === "TimeoutError") {
    return "Could not reach the server. Check the Server URL and your connection.";
  }
  return err instanceof Error ? err.message : String(err);
}

/** Run the browser OAuth sign-in and persist the minted token + identity. */
export async function signInFlow(): Promise<SignInFlowResult> {
  try {
    const current = await streamDeck.settings.getGlobalSettings<GlobalSettings>();
    const result = await signIn(effectiveApiUrl(current), (url) => {
      void streamDeck.system.openUrl(url);
    });
    const merged: GlobalSettings = {
      ...current,
      authToken: result.token,
      discordUserId: result.discordUserId,
      discordUserName: result.discordUserName,
    };
    await streamDeck.settings.setGlobalSettings(merged);
    // apply() also kicks the poller when a settings event arrives; a double
    // kick is safe (the poller's chain-identity guard makes kick idempotent).
    apply(merged);
    return { ok: true, userName: result.discordUserName };
  } catch (err) {
    return { ok: false, error: describeSignInFailure(err) };
  }
}
