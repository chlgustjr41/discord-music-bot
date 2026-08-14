namespace Loupedeck.JackyControlPlugin
{
    using System;
    using System.Diagnostics;
    using System.Threading;
    using System.Threading.Tasks;

    /// <summary>
    /// Press-to-toggle voice command: press once to record, press again to
    /// send. The Stream Deck key is hold-to-talk; a console key press has no
    /// usable key-up, so the toggle replaces the hold — with a 15 s backstop
    /// (recorder event + timer) so a forgotten toggle can never hold the mic
    /// open indefinitely.
    ///
    /// INVARIANT (ported from the Stream Deck voice key): transcripts and
    /// audio bytes are NEVER logged. Log statuses, exception type names and
    /// byte counts only — the response body carries transcribed speech.
    /// </summary>
    public class VoiceCommand : ActionEditorCommand
    {
        private const String MicControl = "microphone";
        private const String LanguageControl = "language";
        private const String DebugControl = "debug";

        /// <summary>The server resolved nothing from the utterance — the fix
        /// is to say it again, not to go look at the bot.</summary>
        private const Int32 NoSpeechStatus = 422;

        /// <summary>The upload was empty: the problem is this end of the wire
        /// and the microphone setting is where to look.</summary>
        private const Int32 NoAudioStatus = 400;

        /// <summary>Per-guild cooldown — the post is already in the channel.</summary>
        private const Int32 JustPostedStatus = 429;

        /// <summary>Voice keys share one label slot: there is one microphone
        /// and one recording, so the answer belongs to whoever spoke.</summary>
        private const String LabelKey = "";

        private static readonly (String Code, String Name)[] Languages =
        {
            ("en", "English"),
            ("ko", "한국어"),
            ("ja", "日本語"),
            ("es", "Español"),
            ("fr", "Français"),
            ("de", "Deutsch"),
            ("zh", "中文"),
        };

        private JackyControlPlugin JackyPlugin => (JackyControlPlugin)this.Plugin;

        /// <summary>The deck key shows results for 4 s; match it.</summary>
        private readonly TransientLabels _labels = new(4000);

        /// <summary>Guards the recorder state machine: a press, the recorder's
        /// max-duration event and the backstop timer can all race to stop the
        /// same recording, and exactly one of them may win.</summary>
        private readonly Object _gate = new();

        private MicRecorder _recorder;
        private Timer _backstop;
        private Boolean _recording;

        /// <summary>Identity of the current recording, bumped under _gate at
        /// every start. Each stop trigger (press, max-duration event, backstop
        /// timer) carries the generation it was registered for, and StopAndSend
        /// no-ops on a mismatch: Timer.Dispose does not cancel an in-flight
        /// callback, and a Task.Run queued for recording A must never be
        /// allowed to kill recording B at ~0 s.</summary>
        private Int32 _generation;

        /// <summary>The per-recording MaxDurationReached handler, kept so the
        /// stale generation's closure can be unsubscribed at stop.</summary>
        private EventHandler _maxDurationHandler;

        /// <summary>Language/debug snapshot taken at record-start, so the whole
        /// press uses one consistent view of the key's settings.</summary>
        private String _language;
        private Boolean _debug;

        public VoiceCommand()
        {
            this.DisplayName = "Voice Command";
            this.Description = "Press to record a voice command, press again to send";
            this.GroupName = "Jacky Music";

            this.ActionEditor.AddControlEx(
                new ActionEditorListbox(MicControl, "Microphone", "Defaults to the first capture device"));
            this.ActionEditor.AddControlEx(new ActionEditorListbox(LanguageControl, "Language"));
            this.ActionEditor.AddControlEx(
                new ActionEditorCheckbox(DebugControl, "Print debug message to Discord"));
            this.ActionEditor.ListboxItemsRequested += this.OnListboxItemsRequested;
        }

        private void OnListboxItemsRequested(Object sender, ActionEditorListboxItemsRequestedEventArgs e)
        {
            if (e.ControlName.EqualsNoCase(MicControl))
            {
                try
                {
                    foreach (var (number, name) in MicRecorder.Devices())
                    {
                        e.AddItem(number.ToString(), name, null);
                    }
                }
                catch (Exception ex)
                {
                    PluginLog.Info($"mic enumeration failed: {ex.GetType().Name}");
                }
            }
            else if (e.ControlName.EqualsNoCase(LanguageControl))
            {
                e.AddItem("", "Auto", "Detect the language automatically");
                foreach (var (code, name) in Languages)
                {
                    e.AddItem(code, name, null);
                }
            }
        }

        protected override Boolean RunCommand(ActionEditorActionParameters parameters)
        {
            Int32 gen;
            lock (this._gate)
            {
                if (!this._recording)
                {
                    this.StartLocked(parameters);
                    this.ActionImageChanged();
                    return true;
                }
                gen = this._generation; // press-stop targets the live recording
            }
            // Second press: stop and send (shared with the 15 s backstops).
            this.StopAndSend(gen);
            return true;
        }

        private void StartLocked(ActionEditorActionParameters parameters)
        {
            parameters.TryGetString(MicControl, out var mic);
            // DirectShow/WASAPI has no "default" capture device — an
            // unconfigured key falls back to device 0 (the production lesson;
            // inventing a "default" is what produced silent zero-byte captures).
            var device = Int32.TryParse(mic, out var n) ? n : 0;
            parameters.TryGetString(LanguageControl, out var language);
            this._language = String.IsNullOrEmpty(language) ? null : language;
            this._debug = parameters.TryGetBoolean(DebugControl, out var d) && d;

            try
            {
                this._recorder ??= new MicRecorder();
                // Start throws for a stale device number (unplugged mic) and
                // leaves the recorder fully reset, so the catch below is the
                // whole recovery — Recording never sticks true.
                this._recorder.Start(device);

                var gen = ++this._generation;
                // Raised from NAudio's capture callback — hop to the pool
                // rather than stopping the device from inside its own event.
                // Per-recording handler so the closure carries THIS recording's
                // generation; unsubscribed again in StopAndSend.
                this._maxDurationHandler = (_, _) => Task.Run(() => this.StopAndSend(gen));
                this._recorder.MaxDurationReached += this._maxDurationHandler;
                this._recording = true;
                // Backstop for the backstop: MaxDurationReached only fires
                // while audio is flowing; a stalled device still gets cut off.
                this._backstop = new Timer(_ => this.StopAndSend(gen), null, MicRecorder.MaxSeconds * 1000, Timeout.Infinite);
            }
            catch (Exception ex)
            {
                PluginLog.Info($"voice start failed: {ex.GetType().Name}");
                this._recording = false;
                this._labels.Set(LabelKey, "Mic error", () => this.ActionImageChanged());
            }
        }

        /// <summary>The single stop path shared by the second press, the
        /// recorder's max-duration event and the backstop timer. The lock's
        /// _recording flag makes it idempotent, and the generation check makes
        /// it precise: a stale trigger queued for an earlier recording no-ops
        /// instead of killing the one that replaced it.</summary>
        private void StopAndSend(Int32 generation)
        {
            Byte[] wav = null;
            var micError = false;
            String language;
            Boolean debug;
            lock (this._gate)
            {
                if (!this._recording || generation != this._generation)
                {
                    return;
                }
                this._recording = false;
                this._backstop?.Dispose();
                this._backstop = null;
                if (this._maxDurationHandler != null)
                {
                    this._recorder.MaxDurationReached -= this._maxDurationHandler;
                    this._maxDurationHandler = null;
                }
                try
                {
                    wav = this._recorder.Stop();
                }
                catch (Exception ex)
                {
                    // Device teardown can throw (mic yanked mid-recording); the
                    // repaint and an answer on the key must still happen. The
                    // instance is discarded too — a recorder whose Stop threw
                    // may still claim Recording, which would turn every later
                    // Start into a silent no-op.
                    PluginLog.Info($"voice stop failed: {ex.GetType().Name}");
                    micError = true;
                    try
                    {
                        this._recorder.Dispose();
                    }
                    catch
                    {
                        // Dispose re-runs Stop; the audio is discarded either way.
                    }
                    this._recorder = null;
                }
                language = this._language;
                debug = this._debug;
            }
            this.ActionImageChanged();
            if (micError)
            {
                this.SetLabel("Mic error");
                return;
            }
            if (wav == null)
            {
                // Header-only WAV: zero audio frames ever arrived. The mic is
                // the thing to look at, not the utterance.
                this.SetLabel("No audio");
                return;
            }
            PluginLog.Info($"voice captured {wav.Length} bytes");   // count only, never content
            _ = this.SendAsync(wav, language, debug);
        }

        private async Task SendAsync(Byte[] wav, String language, Boolean debug)
        {
            try
            {
                var result = await this.JackyPlugin.Client().VoiceCommandAsync(wav, language, debug).ConfigureAwait(false);
                PluginLog.Info("voice request answered 2xx");       // status only — the body carries speech
                this.SetLabel(result.Ok
                    ? (String.IsNullOrEmpty(result.Detail) ? "✓" : "✓ " + result.Detail)
                    : (result.Detail ?? "Failed"));
                // Directives run AFTER the key has its answer, so opening a
                // browser never delays the feedback. `Client` is null on older
                // servers; the directive vocabulary is closed — unknown types
                // are ignored, and only the guard's returned string is opened.
                if (result.Client != null)
                {
                    foreach (var directive in result.Client)
                    {
                        if (directive?.Type != "open_url" || directive.Url == null)
                        {
                            continue;
                        }
                        var safe = UrlGuard.OpenableUrl(directive.Url);
                        if (safe != null)
                        {
                            Process.Start(new ProcessStartInfo(safe) { UseShellExecute = true });
                        }
                    }
                }
            }
            catch (ControlApiException ex)
            {
                PluginLog.Info($"voice failed: {ex.Status}");
                this.SetLabel(ex.Status switch
                {
                    NoSpeechStatus => "Didn't catch that",
                    NoAudioStatus => "No audio",
                    JustPostedStatus => "Just posted",
                    401 => "Sign in",
                    _ => "Failed",
                });
            }
            catch (Exception ex)
            {
                PluginLog.Info($"voice failed: {ex.GetType().Name}");
                this.SetLabel("Failed");
            }
        }

        private void SetLabel(String label)
            => this._labels.Set(LabelKey, label, () => this.ActionImageChanged());

        protected override BitmapImage GetCommandImage(ActionEditorActionParameters parameters, Int32 imageWidth, Int32 imageHeight)
        {
            using var bmp = new BitmapBuilder(imageWidth, imageHeight);
            bmp.Clear(new BitmapColor(26, 26, 46));                    // #1a1a2e
            var accent = new BitmapColor(233, 69, 96);                 // #e94560

            var label = this._labels.Current(LabelKey);
            Boolean recording;
            lock (this._gate)
            {
                recording = this._recording;
            }

            if (label != null)
            {
                bmp.DrawText(label, accent, fontSize: 10);
            }
            else if (recording)
            {
                bmp.DrawText("● REC", accent, fontSize: 14);
            }
            else
            {
                bmp.DrawText("🎤", BitmapColor.White, fontSize: 20);
            }
            return bmp.ToImage();
        }

        /// <summary>Plugin teardown: a recording in flight is stopped and its
        /// audio discarded (never sent, never written anywhere).</summary>
        protected override Boolean OnUnload()
        {
            lock (this._gate)
            {
                this._recording = false;
                this._backstop?.Dispose();
                this._backstop = null;
                this._maxDurationHandler = null;
                this._recorder?.Dispose();
                this._recorder = null;
            }
            return base.OnUnload();
        }
    }
}
