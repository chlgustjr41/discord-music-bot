import {
  action,
  SingletonAction,
  type JsonValue,
  type KeyDownEvent,
  type SendToPluginEvent,
} from "@elgato/streamdeck";
import { handlePiEvent } from "../pi-bridge";
import { getClient } from "../runtime";

type SummonSettings = {
  guildId?: string;
  channelId?: string;
};

@action({ UUID: "com.jacobchoi.jacky-control.summon" })
export class Summon extends SingletonAction<SummonSettings> {
  override onSendToPlugin(ev: SendToPluginEvent<JsonValue, SummonSettings>): Promise<void> {
    return handlePiEvent(ev.payload);
  }

  override async onKeyDown(ev: KeyDownEvent<SummonSettings>): Promise<void> {
    const client = getClient();
    const { guildId, channelId } = await ev.action.getSettings<SummonSettings>();
    if (!client || !guildId || !channelId) return ev.action.showAlert();
    try {
      const result = await client.summon(guildId, channelId);
      await ev.action.setState(result.action === "joined" ? 1 : 0);
      await ev.action.showOk();
    } catch {
      await ev.action.showAlert();
    }
  }
}
