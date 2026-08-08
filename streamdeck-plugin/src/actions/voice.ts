import {
  action,
  SingletonAction,
  type JsonValue,
  type KeyDownEvent,
  type KeyUpEvent,
  type SendToPluginEvent,
  type WillDisappearEvent,
} from "@elgato/streamdeck";
import { MicRecorder } from "../audio-capture";
import { handlePiEvent } from "../pi-bridge";
import { getClient } from "../runtime";

type VoiceSettings = { inputDevice?: string };

const SHOW_RESULT_MS = 4000;

/** Recording state for one physical key. `downs`/`ups` count presses so a
 *  key-up that lands while onKeyDown is still awaiting can be detected. */
type KeyState = {
  recorder: MicRecorder | null;
  heardAudio: boolean;
  downs: number;
  ups: number;
};

@action({ UUID: "com.jacobchoi.jacky-control.voice" })
export class Voice extends SingletonAction<VoiceSettings> {
  // SingletonAction is ONE instance shared by every key of this type, so all
  // recording state is keyed on the action instance id — two voice keys must
  // not share (and orphan) each other's ffmpeg process.
  private readonly keys = new Map<string, KeyState>();

  private stateFor(id: string): KeyState {
    let st = this.keys.get(id);
    if (!st) {
      st = { recorder: null, heardAudio: false, downs: 0, ups: 0 };
      this.keys.set(id, st);
    }
    return st;
  }

  override onSendToPlugin(ev: SendToPluginEvent<JsonValue, VoiceSettings>): Promise<void> {
    return handlePiEvent(ev.payload);
  }

  override async onKeyDown(ev: KeyDownEvent<VoiceSettings>): Promise<void> {
    const st = this.stateFor(ev.action.id);
    const press = ++st.downs;
    const { inputDevice } = await ev.action.getSettings<VoiceSettings>();
    // The SDK does not await handlers, so a tap shorter than that round-trip
    // delivers onKeyUp first. Without this check we would spawn ffmpeg with no
    // key left to stop it, holding the mic open until the 15 s cap — breaking
    // the promise that the mic is open only while the key is held.
    if (st.ups >= press) return;
    // Defence in depth: never leave an earlier press's recorder running.
    const stale = st.recorder;
    st.recorder = null;
    if (stale) void stale.stop().catch(() => {});

    st.heardAudio = false;
    const recorder = new MicRecorder();
    const started = recorder.start(inputDevice, () => {
      // Only now is the device actually delivering audio.
      st.heardAudio = true;
      void ev.action.setTitle("Listening…").catch(() => {});
    });
    if (!started) {
      await ev.action.setTitle("No\nffmpeg");
      await ev.action.showAlert();
      return;
    }
    st.recorder = recorder;
  }

  override async onKeyUp(ev: KeyUpEvent<VoiceSettings>): Promise<void> {
    const st = this.stateFor(ev.action.id);
    st.ups++;
    const recorder = st.recorder;
    st.recorder = null;
    // No recorder: either onKeyDown is still mid-await (the counter above has
    // already told it to abandon this press) or it never started one.
    if (!recorder) return;
    const wav = await recorder.stop();
    if (recorder.spawnFailed) {
      // Zero bytes because the binary is missing, not because of a short hold.
      await ev.action.setTitle("No\nffmpeg");
      await ev.action.showAlert();
      this.clearLater(ev);
      return;
    }
    if (!st.heardAudio || wav.length < 1000) {
      await ev.action.setTitle("Hold\nlonger");
      await ev.action.showAlert();
      this.clearLater(ev);
      return;
    }
    const client = getClient();
    if (!client) {
      await ev.action.setTitle("");
      await ev.action.showAlert();
      return;
    }
    await ev.action.setTitle("Thinking…");
    try {
      const result = await client.voiceCommand(wav);
      await ev.action.setTitle(result.detail || result.transcript);
      if (result.ok) await ev.action.showOk();
      else await ev.action.showAlert();
    } catch {
      await ev.action.setTitle("Failed");
      await ev.action.showAlert();
    }
    this.clearLater(ev);
  }

  /** A key held across a profile switch never gets its onKeyUp, so release the
   *  mic here too rather than waiting out the 15 s cap. */
  override onWillDisappear(ev: WillDisappearEvent<VoiceSettings>): void {
    const st = this.keys.get(ev.action.id);
    if (!st) return;
    this.keys.delete(ev.action.id);
    const recorder = st.recorder;
    st.recorder = null;
    if (recorder) void recorder.stop().catch(() => {});
  }

  private clearLater(ev: KeyDownEvent<VoiceSettings> | KeyUpEvent<VoiceSettings>): void {
    setTimeout(() => void ev.action.setTitle("").catch(() => {}), SHOW_RESULT_MS);
  }
}
