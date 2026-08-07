import { action, SingletonAction, type KeyDownEvent } from "@elgato/streamdeck";
import { getClient } from "../runtime";

@action({ UUID: "com.jacobchoi.jacky-control.volume-down" })
export class VolumeDown extends SingletonAction {
  override async onKeyDown(ev: KeyDownEvent): Promise<void> {
    const client = getClient();
    if (!client) return ev.action.showAlert();
    try {
      await client.volume(-5);
      await ev.action.showOk();
    } catch {
      await ev.action.showAlert();
    }
  }
}
