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
import { letterboxSvg } from "../image";
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
  // undefined = "unknown, re-apply on next tick"; null = "this track has no
  // artwork". Conflating them lets a stale cover survive a track that has none.
  private lastThumbUrl: string | null | undefined = undefined;
  private scrollTimer: ReturnType<typeof setInterval> | null = null;
  private titleSuffix = "";

  private readonly onPoll = (s: PollState): void => {
    // Every branch that shows static text must kill a running scroll timer
    // first, or the timer keeps repainting a marquee frame over the message.
    const setStatic = (text: string): void => {
      this.stopScrolling();
      this.lastTitle = null;
      for (const a of this.actions) a.setTitle(text).catch(() => {});
    };
    if (s.kind === "unconfigured") setStatic("Setup\nneeded");
    else if (s.kind === "unauthorized") setStatic("Auth\nerror");
    else if (s.kind === "offline") setStatic("Offline");
    else if (!s.data.active) setStatic("No\nsession");
    else if (!s.data.title) setStatic(`${s.data.guildName}\n(idle)`);
    else {
      if (s.data.title !== this.lastTitle) {
        this.offset = 0;
        this.lastTitle = s.data.title;
      }
      this.titleSuffix = s.data.paused ? "\n⏸" : "";
      this.startScrolling(s.data.title); // the scroll timer renders the title
    }

    const thumb = s.kind === "data" && s.data.active ? s.data.thumbnail : null;
    if (thumb !== this.lastThumbUrl) {
      this.lastThumbUrl = thumb;
      // Only refetch when the track actually changes, never per poll tick.
      if (thumb) {
        void loadThumbnail(thumb).then((uri) => {
          // A slow fetch may land after another track change — drop it.
          if (uri && this.lastThumbUrl === thumb) {
            for (const a of this.actions) void a.setImage(letterboxSvg(uri)).catch(() => {});
          }
        });
      } else {
        // No artwork / no session: back to the manifest icon so a stale
        // cover never outlives its track.
        for (const a of this.actions) void a.setImage().catch(() => {});
      }
    }
  };

  private renderTitle(): void {
    if (this.lastTitle === null) return;
    const text = marquee(this.lastTitle, this.offset, TITLE_WIDTH) + this.titleSuffix;
    for (const a of this.actions) a.setTitle(text).catch(() => {});
  }

  private startScrolling(title: string): void {
    this.renderTitle();
    // Scroll on a 400 ms clock, not the 5 s poll — otherwise a long title
    // crawls one step per poll and never reads as scrolling.
    if (title.length <= TITLE_WIDTH) {
      this.stopScrolling();
      return;
    }
    if (this.scrollTimer) return;
    this.scrollTimer = setInterval(() => {
      this.offset += 1;
      this.renderTitle();
    }, 400);
  }

  private stopScrolling(): void {
    if (this.scrollTimer) {
      clearInterval(this.scrollTimer);
      this.scrollTimer = null;
    }
  }

  override onSendToPlugin(ev: SendToPluginEvent<JsonValue, JsonObject>): Promise<void> {
    return handlePiEvent(ev.payload);
  }

  override onWillAppear(_ev: WillAppearEvent): void {
    // Unconditional, and back to "unknown" rather than "no artwork": a key
    // appearing alongside an existing one must still get artwork on the next
    // tick. The poller doesn't replay its last state, so without this the new
    // key sits on the manifest icon until the track changes (titles self-heal
    // because they're re-set every tick).
    this.lastThumbUrl = undefined;
    if (++this.visible === 1) {
      this.offset = 0;
      poller.subscribe(this.onPoll);
    }
  }

  override onWillDisappear(_ev: WillDisappearEvent): void {
    if (--this.visible === 0) {
      poller.unsubscribe(this.onPoll);
      this.stopScrolling();
      this.lastTitle = null;
      this.lastThumbUrl = undefined;
    }
  }
}
