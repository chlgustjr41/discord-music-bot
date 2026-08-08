import streamDeck from "@elgato/streamdeck";
import { ControlApiError, JackyClient } from "./api-client";
import { signIn } from "./auth";
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
    const error =
      err instanceof ControlApiError
        ? `sign-in failed (${err.status})`
        : err instanceof Error
          ? err.message
          : String(err);
    return { ok: false, error };
  }
}
