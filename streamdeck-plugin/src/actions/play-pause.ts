import {
  action,
  SingletonAction,
  type DidReceiveSettingsEvent,
  type JsonValue,
  type KeyDownEvent,
  type SendToPluginEvent,
  type WillAppearEvent,
  type WillDisappearEvent,
} from "@elgato/streamdeck";
import { marquee } from "../format";
import { letterboxSvg } from "../image";
import { handlePiEvent } from "../pi-bridge";
import { getClient, poller } from "../runtime";
import type { PollState } from "../poller";
import { loadThumbnail } from "../thumbnail";

const TITLE_WIDTH = 9;

export type PlayPauseSettings = { showTitle?: boolean; showArtwork?: boolean };

/** Per-key display state. Now Playing kept title/offset/thumb as single
 *  instance fields, which was fine when the key had no settings; here two keys
 *  can be configured differently, so every field that a setting can steer has
 *  to live beside that key's settings or one key's render would decide the
 *  other's. */
type KeyState = {
  settings: PlayPauseSettings;
  offset: number;
  lastTitle: string | null;
  // undefined = "never touched this key's image"; null = "this track has no
  // artwork". Conflating them either lets a stale cover survive a track that
  // has none, or makes an options-off key call setImage it never needed to.
  lastThumbUrl: string | null | undefined;
};

@action({ UUID: "com.jacobchoi.jacky-control.play-pause" })
export class PlayPause extends SingletonAction<PlayPauseSettings> {
  private readonly keys = new Map<string, KeyState>();
  // Shared, because it is derived from the shared poll state rather than from
  // any one key's settings.
  private titleSuffix = "";
  private scrollTimer: ReturnType<typeof setInterval> | null = null;

  private readonly onPoll = (s: PollState): void => {
    let staticText: string | null = null;
    let track: string | null = null;
    if (s.kind === "unconfigured") staticText = "Setup\nneeded";
    else if (s.kind === "unauthorized") staticText = "Auth\nerror";
    else if (s.kind === "offline") staticText = "Offline";
    else if (!s.data.active) staticText = "No\nsession";
    else if (!s.data.title) staticText = `${s.data.guildName}\n(idle)`;
    else track = s.data.title;

    const paused = s.kind === "data" && s.data.active && s.data.paused;
    this.titleSuffix = paused ? "\n⏸" : "";
    // Any static message must kill a running scroll first, or the timer keeps
    // repainting a marquee frame over the message.
    if (staticText !== null) this.stopScrolling();

    const thumb = s.kind === "data" && s.data.active ? s.data.thumbnail : null;

    for (const a of this.actions) {
      const st = this.keys.get(a.id);
      if (!st) continue;
      if (a.isKey() && s.kind === "data" && s.data.active) {
        a.setState(paused ? 1 : 0).catch(() => {}); // manifest state 1 = paused icon
      }

      if (st.settings.showTitle) {
        if (staticText !== null) {
          st.lastTitle = null;
          a.setTitle(staticText).catch(() => {});
        } else if (track !== st.lastTitle) {
          st.offset = 0;
          st.lastTitle = track;
        }
      } else if (st.lastTitle !== null) {
        // The option was switched off with a title on the key; clear it rather
        // than leaving the last frame frozen there.
        st.lastTitle = null;
        a.setTitle("").catch(() => {});
      }

      if (st.settings.showArtwork) {
        if (thumb !== st.lastThumbUrl) {
          st.lastThumbUrl = thumb;
          // Only refetch when the track actually changes, never per poll tick.
          if (thumb) {
            void loadThumbnail(thumb).then((uri) => {
              // A slow fetch may land after another track change — drop it.
              if (uri && st.lastThumbUrl === thumb) {
                void a.setImage(letterboxSvg(uri)).catch(() => {});
              }
            });
          } else {
            void a.setImage().catch(() => {});
          }
        }
      } else if (st.lastThumbUrl !== undefined) {
        // Back to the manifest image for the current state. Guarded on
        // "undefined" so a key with the option never enabled issues no
        // setImage at all.
        st.lastThumbUrl = undefined;
        void a.setImage().catch(() => {});
      }
    }

    if (track !== null) this.startScrolling();
  };

  private renderTitles(): void {
    for (const a of this.actions) {
      const st = this.keys.get(a.id);
      if (!st || !st.settings.showTitle || st.lastTitle === null) continue;
      const text = marquee(st.lastTitle, st.offset, TITLE_WIDTH) + this.titleSuffix;
      a.setTitle(text).catch(() => {});
    }
  }

  private scrolling(): boolean {
    for (const st of this.keys.values()) {
      if (st.settings.showTitle && st.lastTitle !== null && st.lastTitle.length > TITLE_WIDTH) {
        return true;
      }
    }
    return false;
  }

  private startScrolling(): void {
    this.renderTitles();
    // Scroll on a 400 ms clock, not the 5 s poll — otherwise a long title
    // crawls one step per poll and never reads as scrolling.
    if (!this.scrolling()) {
      this.stopScrolling();
      return;
    }
    if (this.scrollTimer) return;
    this.scrollTimer = setInterval(() => {
      for (const st of this.keys.values()) {
        if (st.settings.showTitle && st.lastTitle !== null) st.offset += 1;
      }
      this.renderTitles();
    }, 400);
  }

  private stopScrolling(): void {
    if (this.scrollTimer) {
      clearInterval(this.scrollTimer);
      this.scrollTimer = null;
    }
  }

  override onWillAppear(ev: WillAppearEvent<PlayPauseSettings>): void {
    const first = this.keys.size === 0;
    this.keys.set(ev.action.id, {
      settings: ev.payload.settings ?? {},
      offset: 0,
      lastTitle: null,
      lastThumbUrl: undefined,
    });
    if (first) poller.subscribe(this.onPoll);
  }

  override onWillDisappear(ev: WillDisappearEvent<PlayPauseSettings>): void {
    this.keys.delete(ev.action.id);
    if (this.keys.size === 0) {
      poller.unsubscribe(this.onPoll);
      this.stopScrolling();
    }
  }

  override onDidReceiveSettings(ev: DidReceiveSettingsEvent<PlayPauseSettings>): void {
    const st = this.keys.get(ev.action.id);
    // Only the settings change; the title offset and the thumbnail identity
    // belong to the track, not to the checkbox that was just toggled.
    if (st) st.settings = ev.payload.settings ?? {};
  }

  override onSendToPlugin(ev: SendToPluginEvent<JsonValue, PlayPauseSettings>): Promise<void> {
    return handlePiEvent(ev.payload);
  }

  override async onKeyDown(ev: KeyDownEvent<PlayPauseSettings>): Promise<void> {
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
