import streamDeck, { type JsonValue } from "@elgato/streamdeck";
import { getClient, signInFlow } from "./runtime";
import type { GlobalSettings } from "./settings";

/** Payloads the property inspector sends via `sendToPlugin`. */
type PiEvent = { event?: string };

function reply(payload: JsonValue): Promise<void> {
  // The PI that sent the event is the (only) current property inspector.
  return streamDeck.ui.current?.sendToPropertyInspector(payload) ?? Promise.resolve();
}

/** Shared PI→plugin message handler; every action's onSendToPlugin delegates
 *  here with its event payload (the handler needs no per-action context). */
export async function handlePiEvent(rawPayload: JsonValue): Promise<void> {
  const payload = rawPayload as PiEvent | null;
  switch (payload?.event) {
    case "sign-in": {
      const result = await signInFlow();
      await reply({
        event: "auth-status",
        ok: result.ok,
        userName: result.userName ?? null,
        error: result.error ?? null,
      });
      break;
    }
    case "get-channels": {
      const client = getClient();
      if (!client) {
        await reply({ event: "channels-error", error: "not signed in" });
        break;
      }
      try {
        const data = await client.channels();
        await reply({ event: "channels", data });
      } catch (err) {
        const error = err instanceof Error ? err.message : String(err);
        await reply({ event: "channels-error", error });
      }
      break;
    }
    case "get-playlists": {
      const client = getClient();
      if (!client) {
        await reply({ event: "playlists-error", error: "not signed in" });
        break;
      }
      try {
        const data = await client.playlists();
        await reply({ event: "playlists", data });
      } catch (err) {
        const error = err instanceof Error ? err.message : String(err);
        await reply({ event: "playlists-error", error });
      }
      break;
    }
    case "get-audio-devices": {
      try {
        const { listAudioDevices } = await import("./audio-devices");
        await reply({ event: "audio-devices", data: await listAudioDevices() });
      } catch (err) {
        const error = err instanceof Error ? err.message : String(err);
        await reply({ event: "audio-devices-error", error });
      }
      break;
    }
    case "get-status": {
      const s = await streamDeck.settings.getGlobalSettings<GlobalSettings>();
      await reply({
        event: "auth-status",
        ok: Boolean(s.authToken),
        userName: s.discordUserName ?? null,
      });
      break;
    }
  }
}
