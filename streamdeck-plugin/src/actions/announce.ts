import {
  action,
  SingletonAction,
  type JsonValue,
  type KeyDownEvent,
  type SendToPluginEvent,
} from "@elgato/streamdeck";
import { ControlApiError } from "../api-client";
import { handlePiEvent } from "../pi-bridge";
import { getClient } from "../runtime";

type AnnounceSettings = { command?: string };

const SHOW_RESULT_MS = 4000;

/** The per-guild cooldown: the post the user asked for happened seconds ago.
 *  Its own message because the fix is "wait", not "go check the bot". */
const JUST_POSTED_STATUS = 429;

/** Greedy word-wrap for a key title: the server's `detail` strings are short
 *  sentences ("Nothing is playing"), and a 72-pixel key renders roughly ten
 *  characters per line before the tail is clipped. */
function wrapTitle(text: string, width = 10): string {
  const lines: string[] = [];
  let line = "";
  for (const word of text.split(/\s+/)) {
    if (!line) line = word;
    else if (line.length + 1 + word.length <= width) line += " " + word;
    else {
      lines.push(line);
      line = word;
    }
  }
  if (line) lines.push(line);
  return lines.join("\n");
}

@action({ UUID: "com.jacobchoi.jacky-control.announce" })
export class Announce extends SingletonAction<AnnounceSettings> {
  override onSendToPlugin(ev: SendToPluginEvent<JsonValue, AnnounceSettings>): Promise<void> {
    return handlePiEvent(ev.payload);
  }

  override async onKeyDown(ev: KeyDownEvent<AnnounceSettings>): Promise<void> {
    const client = getClient();
    const { command } = await ev.action.getSettings<AnnounceSettings>();
    // An unconfigured key posts nothing: there is no default command, exactly
    // like a Summon key with no channel chosen.
    if (!client || !command) return ev.action.showAlert();
    try {
      const result = await client.announce(command);
      if (result.ok) return ev.action.showOk();
      // 200 with ok:false: the server answered the person at the deck ("Queue
      // is empty") — the key is where that answer must land.
      await ev.action.setTitle(wrapTitle(result.detail || "Failed"));
      await ev.action.showAlert();
      this.clearLater(ev);
    } catch (err) {
      const status = err instanceof ControlApiError ? err.status : null;
      if (status === JUST_POSTED_STATUS) {
        // Not a failure to render as one: the post the user wanted is already
        // in the channel.
        await ev.action.setTitle("Just\nposted");
        this.clearLater(ev);
      }
      await ev.action.showAlert();
    }
  }

  /** Same idea as the Voice key: the title is transient feedback, so hand the
   *  key back to its icon after the user has had a chance to read it. */
  private clearLater(ev: KeyDownEvent<AnnounceSettings>): void {
    setTimeout(() => void ev.action.setTitle("").catch(() => {}), SHOW_RESULT_MS);
  }
}
