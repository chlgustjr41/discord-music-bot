import {
  action,
  SingletonAction,
  type WillAppearEvent,
  type WillDisappearEvent,
} from "@elgato/streamdeck";
import { marquee } from "../format";
import type { PollState } from "../poller";
import { poller } from "../runtime";

const TITLE_WIDTH = 9;

@action({ UUID: "com.jacobchoi.jacky-control.now-playing" })
export class NowPlaying extends SingletonAction {
  private visible = 0;
  private offset = 0;

  private readonly onPoll = (s: PollState): void => {
    let text: string;
    if (s.kind === "unconfigured") text = "Setup\nneeded";
    else if (s.kind === "unauthorized") text = "Auth\nerror";
    else if (s.kind === "offline") text = "Offline";
    else if (!s.data.active) text = "No\nsession";
    else if (!s.data.title) text = `${s.data.guildName}\n(idle)`;
    else {
      text = marquee(s.data.title, this.offset, TITLE_WIDTH);
      if (s.data.paused) text += "\n⏸";
      this.offset += 2;
    }
    for (const a of this.actions) void a.setTitle(text);
  };

  override onWillAppear(_ev: WillAppearEvent): void {
    if (++this.visible === 1) {
      this.offset = 0;
      poller.subscribe(this.onPoll);
    }
  }

  override onWillDisappear(_ev: WillDisappearEvent): void {
    if (--this.visible === 0) poller.unsubscribe(this.onPoll);
  }
}
