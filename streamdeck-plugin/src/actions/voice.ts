import {
  action,
  SingletonAction,
  type JsonValue,
  type KeyDownEvent,
  type KeyUpEvent,
  type SendToPluginEvent,
} from "@elgato/streamdeck";
import { MicRecorder } from "../audio-capture";
import { handlePiEvent } from "../pi-bridge";
import { getClient } from "../runtime";

type VoiceSettings = { inputDevice?: string };

const SHOW_RESULT_MS = 4000;

@action({ UUID: "com.jacobchoi.jacky-control.voice" })
export class Voice extends SingletonAction<VoiceSettings> {
  private recorder: MicRecorder | null = null;
  private heardAudio = false;

  override onSendToPlugin(ev: SendToPluginEvent<JsonValue, VoiceSettings>): Promise<void> {
    return handlePiEvent(ev.payload);
  }

  override async onKeyDown(ev: KeyDownEvent<VoiceSettings>): Promise<void> {
    const { inputDevice } = await ev.action.getSettings<VoiceSettings>();
    this.heardAudio = false;
    this.recorder = new MicRecorder();
    const started = this.recorder.start(inputDevice, () => {
      // Only now is the device actually delivering audio.
      this.heardAudio = true;
      void ev.action.setTitle("Listening…").catch(() => {});
    });
    if (!started) {
      this.recorder = null;
      await ev.action.setTitle("No\nffmpeg");
      await ev.action.showAlert();
    }
  }

  override async onKeyUp(ev: KeyUpEvent<VoiceSettings>): Promise<void> {
    const recorder = this.recorder;
    this.recorder = null;
    if (!recorder) return;
    const wav = await recorder.stop();
    if (!this.heardAudio || wav.length < 1000) {
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

  private clearLater(ev: KeyDownEvent<VoiceSettings> | KeyUpEvent<VoiceSettings>): void {
    setTimeout(() => void ev.action.setTitle("").catch(() => {}), SHOW_RESULT_MS);
  }
}
