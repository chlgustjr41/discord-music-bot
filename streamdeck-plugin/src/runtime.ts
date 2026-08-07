import streamDeck from "@elgato/streamdeck";
import { ControlApiError, JackyClient } from "./api-client";
import { SessionPoller } from "./poller";
import { settingsReady, type GlobalSettings } from "./settings";

let client: JackyClient | null = null;

export function getClient(): JackyClient | null {
  return client;
}

export const poller = new SessionPoller(async () => {
  if (!client) throw new ControlApiError(0); // -> "unconfigured"
  return client.nowPlaying();
});

/** Load global settings and rebuild the client whenever they change. */
export async function initRuntime(): Promise<void> {
  const apply = (s: GlobalSettings) => {
    client = settingsReady(s) ? new JackyClient(s) : null;
  };
  streamDeck.settings.onDidReceiveGlobalSettings<GlobalSettings>((ev) =>
    apply(ev.settings),
  );
  apply(await streamDeck.settings.getGlobalSettings<GlobalSettings>());
}
