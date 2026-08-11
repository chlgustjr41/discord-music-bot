import streamDeck, {
  action,
  SingletonAction,
  type JsonValue,
  type KeyDownEvent,
  type KeyUpEvent,
  type SendToPluginEvent,
  type WillDisappearEvent,
} from "@elgato/streamdeck";
import { ControlApiError } from "../api-client";
import { MicRecorder } from "../audio-capture";
import { resolveInputDevice } from "../audio-devices";
import { handlePiEvent } from "../pi-bridge";
import { getClient } from "../runtime";
import { openableUrl } from "../url-guard";

type VoiceSettings = { inputDevice?: string; language?: string; debug?: boolean };

const SHOW_RESULT_MS = 4000;

/** The blind spot that made the microphone bug take two rounds to find: the
 *  key rendered one word and the capture left no trace at all, so "the device
 *  never opened" and "the server misheard" were indistinguishable from
 *  outside. Written at INFO, matching the artwork scope, because the SDK's
 *  default level outside a debugger is INFO.
 *
 *  INVARIANT: nothing here logs the transcript or the response body. The body
 *  carries transcribed speech; the status does not. Log statuses only. */
const log = streamDeck.logger.createScope("voice");

/** The server resolved nothing from the utterance and dispatched nothing.
 *  Worth its own message: this is the failure the user fixes by speaking
 *  again, not by going to look at whether the bot is up. */
const NO_SPEECH_STATUS = 422;

/** Recording state for one physical key. `downs`/`ups` count presses so a
 *  key-up that lands while onKeyDown is still awaiting can be detected. */
type KeyState = {
  recorder: MicRecorder | null;
  heardAudio: boolean;
  downs: number;
  ups: number;
  /** Captured on key-down from the same getSettings() read as the microphone,
   *  so the whole press uses one consistent snapshot of the key's settings. */
  language?: string;
  /** Same snapshot: asks the server to post what it heard and how it resolved
   *  into the session's Discord channel. Off unless the key opted in. */
  debug?: boolean;
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
    const { inputDevice, language, debug } = await ev.action.getSettings<VoiceSettings>();
    // The SDK does not await handlers, so a tap shorter than that round-trip
    // delivers onKeyUp first. Without this check we would spawn ffmpeg with no
    // key left to stop it, holding the mic open until the 15 s cap — breaking
    // the promise that the mic is open only while the key is held.
    // Also re-check identity: onWillDisappear may have deleted (and a later
    // press recreated) this key's state while we awaited getSettings, which
    // would strand the recorder we are about to spawn in an unreachable state
    // object with nothing able to stop it.
    if (st.ups >= press || this.keys.get(ev.action.id) !== st) return;
    // A second await, so the same release check has to run again after it: an
    // unconfigured key pays a device enumeration here, which is far slower
    // than the settings round-trip and just as easy to tap through.
    const device = await resolveInputDevice(inputDevice);
    if (st.ups >= press || this.keys.get(ev.action.id) !== st) return;
    if (!device) {
      // Nothing to invent. `audio=default` is not a DirectShow device, and
      // spawning against it is what produced a silent zero-byte capture.
      log.warn(`${ev.action.id}: no audio input device is available`);
      await ev.action.setTitle("No\nmic");
      await ev.action.showAlert();
      return;
    }
    log.info(
      `${ev.action.id}: recording from ${device}` +
        (inputDevice ? "" : " (auto-picked; the key has none configured)"),
    );
    // Defence in depth: never leave an earlier press's recorder running.
    const stale = st.recorder;
    st.recorder = null;
    if (stale) void stale.stop().catch(() => {});

    st.heardAudio = false;
    st.language = language;
    st.debug = debug;
    const recorder = new MicRecorder();
    const started = recorder.start(device, () => {
      // Only now is the device actually delivering audio.
      st.heardAudio = true;
      void ev.action.setTitle("Listening…").catch(() => {});
    });
    if (!started) {
      log.error(`${ev.action.id}: no ffmpeg binary could be resolved`);
      await ev.action.setTitle("No\nffmpeg");
      await ev.action.showAlert();
      return;
    }
    st.recorder = recorder;
  }

  override async onKeyUp(ev: KeyUpEvent<VoiceSettings>): Promise<void> {
    const st = this.stateFor(ev.action.id);
    // "every outstanding press is cancelled" — idempotent, so a key-up with no
    // matching key-down (state recreated after onWillDisappear) cannot drift
    // the counters into permanently suppressing every future press.
    st.ups = st.downs;
    const recorder = st.recorder;
    st.recorder = null;
    // No recorder: either onKeyDown is still mid-await (the counter above has
    // already told it to abandon this press) or it never started one.
    if (!recorder) return;
    const wav = await recorder.stop();
    log.info(`${ev.action.id}: captured ${wav.length} bytes`);
    if (recorder.spawnFailed) {
      // Zero bytes because the binary is missing, not because of a short hold.
      log.error(`${ev.action.id}: ffmpeg never launched`);
      await ev.action.setTitle("No\nffmpeg");
      await ev.action.showAlert();
      this.clearLater(ev);
      return;
    }
    if (recorder.micFailed) {
      // ffmpeg launched and then died on its own. Also zero bytes, which is
      // why this used to read as "Hold longer" — the reason is in stderr, and
      // stderr is the only place it exists.
      log.error(
        `${ev.action.id}: ffmpeg exited ${recorder.exitCode}: ` +
          `${recorder.stderr.trim() || "(nothing on stderr)"}`,
      );
      await ev.action.setTitle("Mic\nerror");
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
      const result = await client.voiceCommand(wav, {
        language: st.language,
        debug: st.debug,
      });
      // Status only — the body carries transcribed speech (see the INVARIANT
      // on `log`), so there is nothing here to widen this line with.
      log.info(`${ev.action.id}: the server answered 2xx`);
      await ev.action.setTitle(result.detail || result.transcript);
      if (result.ok) await ev.action.showOk();
      else await ev.action.showAlert();
      // Directives execute AFTER the key has rendered, so opening a browser
      // never delays the feedback the user is waiting on. Unknown types are
      // ignored rather than dispatched generically — the directive vocabulary
      // is closed, exactly like the action vocabulary.
      // The response is not schema-validated, so every shape here is checked
      // rather than trusted: a non-array `client` would otherwise throw out of
      // the loop into the catch below and report "Failed" for a command the
      // server already carried out, prompting the user to run it again.
      const directives = Array.isArray(result.client) ? result.client : [];
      for (const directive of directives) {
        if (directive?.type !== "open_url" || typeof directive.url !== "string") continue;
        const safe = openableUrl(directive.url);
        if (safe) await streamDeck.system.openUrl(safe);
      }
    } catch (err) {
      // "Nothing was resolvable" is not "the bot broke", and the user's next
      // move differs: say it again, versus go and look. Every other status —
      // and anything that never reached the server — stays "Failed".
      const status = err instanceof ControlApiError ? err.status : null;
      log.warn(
        `${ev.action.id}: the voice request failed with ` +
          `${status === null ? "no response at all" : `status ${status}`}`,
      );
      const title = status === NO_SPEECH_STATUS ? "Didn't\ncatch that" : "Failed";
      await ev.action.setTitle(title);
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
