import streamDeck, {
  action,
  SingletonAction,
  type JsonObject,
  type JsonValue,
  type KeyDownEvent,
  type SendToPluginEvent,
} from "@elgato/streamdeck";
import { handlePiEvent } from "../pi-bridge";
import { getClient } from "../runtime";

@action({ UUID: "com.jacobchoi.jacky-control.dashboard" })
export class Dashboard extends SingletonAction {
  override onSendToPlugin(ev: SendToPluginEvent<JsonValue, JsonObject>): Promise<void> {
    return handlePiEvent(ev.payload);
  }

  override async onKeyDown(ev: KeyDownEvent): Promise<void> {
    const client = getClient();
    if (!client) return ev.action.showAlert();
    try {
      const { active, url } = await client.dashboardUrl();
      await streamDeck.system.openUrl(url);
      // Still opens the entry page when there's no session — the flash just
      // says "there was nothing to jump to".
      if (active) await ev.action.showOk();
      else await ev.action.showAlert();
    } catch {
      await ev.action.showAlert();
    }
  }
}
