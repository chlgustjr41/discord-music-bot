namespace Loupedeck.JackyControlPlugin
{
    using System;
    using System.Threading.Tasks;

    /// <summary>
    /// Play/Pause key with a live image: track artwork under a dark title band
    /// while a session is active, a glyph or "Sign in" otherwise, and a pause
    /// marker when the player is paused. ALL drawing lives in GetCommandImage
    /// so post-hardware tweaks stay one-file.
    /// </summary>
    public class PlayPauseCommand : PluginDynamicCommand
    {
        private JackyControlPlugin JackyPlugin => (JackyControlPlugin)this.Plugin;

        /// <summary>Latest poll state; null before the first poll lands.</summary>
        private PollState _lastState;

        /// <summary>The thumbnail URL the cached artwork belongs to. Tracked
        /// separately from the bytes so a slow fetch that lands after another
        /// track change can be dropped, and so a track without artwork is not
        /// refetched every tick.</summary>
        private String _artUrl;

        /// <summary>Fetched artwork bytes for <see cref="_artUrl"/>, or null
        /// while no image is in force (fetch pending, failed, or no artwork).</summary>
        private Byte[] _artwork;

        public PlayPauseCommand() : base("Play / Pause", "Toggle playback", "Jacky Music") { }

        // OnLoad runs after the SDK has set this.Plugin, so the poller exists.
        protected override Boolean OnLoad()
        {
            this.JackyPlugin.Poller.Subscribe(this.OnPoll);
            return base.OnLoad();
        }

        protected override Boolean OnUnload()
        {
            this.JackyPlugin.Poller.Unsubscribe(this.OnPoll);
            return base.OnUnload();
        }

        private void OnPoll(PollState state)
        {
            this._lastState = state;
            var thumb = state is PollState.Data data && data.NowPlaying.Active ? data.NowPlaying.Thumbnail : null;
            if (thumb != this._artUrl)
            {
                this._artUrl = thumb;
                this._artwork = null;
                if (thumb != null)
                {
                    // Refetch only when the track actually changes, never per tick.
                    _ = this.LoadArtworkAsync(thumb);
                }
            }
            this.ActionImageChanged();
        }

        private async Task LoadArtworkAsync(String url)
        {
            var bytes = await Thumbnails.LoadAsync(url, this.JackyPlugin.Http).ConfigureAwait(false);
            if (bytes == null)
            {
                PluginLog.Info("artwork fetch returned nothing"); // URL/status stay out of the log; nothing sensitive to add
                return;
            }
            if (this._artUrl != url)
            {
                return; // a slow fetch that lost its track
            }
            this._artwork = bytes;
            this.ActionImageChanged();
        }

        protected override void RunCommand(String actionParameter) => _ = this.RunAsync();

        private async Task RunAsync()
        {
            try
            {
                await this.JackyPlugin.Client().PlayPauseAsync().ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                // Status codes and type names only — never bodies or tokens.
                PluginLog.Info($"play-pause failed: {(ex as ControlApiException)?.Status.ToString() ?? ex.GetType().Name}");
            }
        }

        protected override BitmapImage GetCommandImage(String actionParameter, PluginImageSize imageSize)
        {
            using var bmp = new BitmapBuilder(imageSize);
            var dark = new BitmapColor(26, 26, 46);                        // #1a1a2e, the family idiom
            var accent = new BitmapColor(233, 69, 96);                     // #e94560
            bmp.Clear(dark);

            var state = this._lastState;
            if (state is not PollState.Data data || !data.NowPlaying.Active)
            {
                bmp.DrawText(state is PollState.Unauthorized ? "Sign in" : "♪", accent, fontSize: 14);
                return bmp.ToImage();
            }

            if (this._artwork != null && BitmapImage.TryCreateFromArray(this._artwork, out var art))
            {
                bmp.SetBackgroundImage(art, resize: true);                 // scaled to the key
                bmp.FillRectangle(0, bmp.Height - 26, bmp.Width, 26, new BitmapColor(26, 26, 46, 200));
            }

            var lines = KeyText.TitleLines(data.NowPlaying.Title, maxCharsPerLine: 11, maxLines: 2);
            for (var i = 0; i < lines.Length; i++)
            {
                bmp.DrawText(lines[i], 0, bmp.Height - 26 + (i * 12), bmp.Width, 12, BitmapColor.White, fontSize: 9);
            }

            if (data.NowPlaying.Paused)
            {
                bmp.DrawText("⏸", 2, 2, 16, 16, accent, fontSize: 12);
            }
            return bmp.ToImage();
        }
    }
}
