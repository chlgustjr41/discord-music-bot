import {
  action,
  SingletonAction,
  type JsonObject,
  type JsonValue,
  type SendToPluginEvent,
  type WillAppearEvent,
  type WillDisappearEvent,
} from "@elgato/streamdeck";
import { marquee } from "../format";
import { handlePiEvent } from "../pi-bridge";
import type { PollState } from "../poller";
import { poller } from "../runtime";
import { loadThumbnail } from "../thumbnail";

const TITLE_WIDTH = 9;

@action({ UUID: "com.jacobchoi.jacky-control.now-playing" })
export class NowPlaying extends SingletonAction {
  private visible = 0;
  private offset = 0;
  private lastTitle: string | null = null;
  private lastThumbUrl: string | null = null;

  private readonly onPoll = (s: PollState): void => {
    let text: string;
    if (s.kind === "unconfigured") text = "Setup\nneeded";
    else if (s.kind === "unauthorized") text = "Auth\nerror";
    else if (s.kind === "offline") text = "Offline";
    else if (!s.data.active) text = "No\nsession";
    else if (!s.data.title) text = `${s.data.guildName}\n(idle)`;
    else {
      if (s.data.title !== this.lastTitle) {
        this.offset = 0;
        this.lastTitle = s.data.title;
      }
      text = marquee(s.data.title, this.offset, TITLE_WIDTH);
      if (s.data.paused) text += "\n⏸";
      this.offset += 2;
    }
    for (const a of this.actions) a.setTitle(text).catch(() => {});

    const thumb = s.kind === "data" && s.data.active ? s.data.thumbnail : null;
    if (thumb !== this.lastThumbUrl) {
      this.lastThumbUrl = thumb;
      // Only refetch when the track actually changes, never per poll tick.
      if (thumb) {
        void loadThumbnail(thumb).then((uri) => {
          // A slow fetch may land after another track change — drop it.
          if (uri && this.lastThumbUrl === thumb) {
            for (const a of this.actions) void a.setImage(uri).catch(() => {});
          }
        });
      } else {
        // No artwork / no session: back to the manifest icon so a stale
        // cover never outlives its track.
        for (const a of this.actions) void a.setImage().catch(() => {});
      }
    }
  };

  override onSendToPlugin(ev: SendToPluginEvent<JsonValue, JsonObject>): Promise<void> {
    return handlePiEvent(ev.payload);
  }

  override onWillAppear(_ev: WillAppearEvent): void {
    // Unconditional: a key appearing alongside an existing one must still get
    // artwork on the next tick. The poller doesn't replay its last state, so
    // without this the new key sits on the manifest icon until the track
    // changes (titles self-heal because they're re-set every tick).
    this.lastThumbUrl = null;
    if (++this.visible === 1) {
      this.offset = 0;
      poller.subscribe(this.onPoll);
    }
  }

  override onWillDisappear(_ev: WillDisappearEvent): void {
    if (--this.visible === 0) {
      poller.unsubscribe(this.onPoll);
      this.lastThumbUrl = null;
    }
  }
}
