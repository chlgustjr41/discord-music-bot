import {
  action,
  SingletonAction,
  type JsonValue,
  type KeyDownEvent,
  type SendToPluginEvent,
} from "@elgato/streamdeck";
import { handlePiEvent } from "../pi-bridge";
import { getClient } from "../runtime";

type PlaylistSettings = {
  guildId?: string;
  playlistName?: string;
};

@action({ UUID: "com.jacobchoi.jacky-control.playlist" })
export class Playlist extends SingletonAction<PlaylistSettings> {
  override onSendToPlugin(ev: SendToPluginEvent<JsonValue, PlaylistSettings>): Promise<void> {
    return handlePiEvent(ev.payload);
  }

  override async onKeyDown(ev: KeyDownEvent<PlaylistSettings>): Promise<void> {
    const client = getClient();
    const { guildId, playlistName } = await ev.action.getSettings<PlaylistSettings>();
    if (!client || !guildId || !playlistName) return ev.action.showAlert();
    try {
      await client.playPlaylist(guildId, playlistName);
      await ev.action.showOk();
    } catch {
      await ev.action.showAlert();
    }
  }
}
