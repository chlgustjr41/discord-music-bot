import { action, SingletonAction, type KeyDownEvent } from "@elgato/streamdeck";
import { getClient } from "../runtime";

@action({ UUID: "com.jacobchoi.jacky-control.skip" })
export class Skip extends SingletonAction {
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
