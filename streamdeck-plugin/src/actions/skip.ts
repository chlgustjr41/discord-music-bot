import {
  action,
  SingletonAction,
  type JsonObject,
  type JsonValue,
  type KeyDownEvent,
  type SendToPluginEvent,
} from "@elgato/streamdeck";
import { handlePiEvent } from "../pi-bridge";
import { getClient } from "../runtime";

@action({ UUID: "com.jacobchoi.jacky-control.skip" })
export class Skip extends SingletonAction {
  override onSendToPlugin(ev: SendToPluginEvent<JsonValue, JsonObject>): Promise<void> {
    return handlePiEvent(ev.payload);
  }

  override async onKeyDown(ev: KeyDownEvent): Promise<void> {
    const client = getClient();
    if (!client) return ev.action.showAlert();
    try {
      await client.skip();
      await ev.action.showOk();
    } catch {
      await ev.action.showAlert();
    }
  }
}
