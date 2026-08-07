import {
  action,
  SingletonAction,
  type KeyDownEvent,
  type WillAppearEvent,
  type WillDisappearEvent,
} from "@elgato/streamdeck";
import { getClient, poller } from "../runtime";
import type { PollState } from "../poller";

@action({ UUID: "com.jacobchoi.jacky-control.play-pause" })
export class PlayPause extends SingletonAction {
  private visible = 0;

  private readonly onPoll = (s: PollState): void => {
    if (s.kind !== "data" || !s.data.active) return;
    const state = s.data.paused ? 1 : 0; // manifest state 1 = paused icon
    for (const a of this.actions) {
      if (a.isKey()) void a.setState(state);
    }
  };

  override onWillAppear(_ev: WillAppearEvent): void {
    if (++this.visible === 1) poller.subscribe(this.onPoll);
  }

  override onWillDisappear(_ev: WillDisappearEvent): void {
    if (--this.visible === 0) poller.unsubscribe(this.onPoll);
  }

  override async onKeyDown(ev: KeyDownEvent): Promise<void> {
    const client = getClient();
    if (!client) return ev.action.showAlert();
    try {
      await client.playPause();
      await ev.action.showOk();
    } catch {
      await ev.action.showAlert();
    }
  }
}
