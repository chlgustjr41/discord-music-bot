namespace Loupedeck.JackyControlPlugin
{
    using System;
    using System.Collections.Generic;
    using System.Threading.Tasks;

    /// <summary>
    /// Parameterised "post to Discord" fan-out (session code / now playing /
    /// queue / status). Feedback lands on the key itself as a transient label:
    /// the person pressing the key is the person the server is answering
    /// ("Queue is empty"), so the key is where that answer must land.
    /// </summary>
    public class AnnounceCommand : PluginDynamicCommand
    {
        /// <summary>Per-guild cooldown: the post already happened seconds ago.
        /// Its own message because the fix is "wait", not "go check the bot".</summary>
        private const Int32 JustPostedStatus = 429;

        private JackyControlPlugin JackyPlugin => (JackyControlPlugin)this.Plugin;

        private readonly TransientLabels _labels = new();

        private static readonly Dictionary<String, String> ShortNames = new()
        {
            ["session"] = "Session",
            ["nowplaying"] = "Now Playing",
            ["queue"] = "Queue",
            ["status"] = "Status",
        };

        public AnnounceCommand() : base()
        {
            this.AddParameter("session", "Post session code", "Jacky Music###Post to Discord");
            this.AddParameter("nowplaying", "Post now playing", "Jacky Music###Post to Discord");
            this.AddParameter("queue", "Post queue", "Jacky Music###Post to Discord");
            this.AddParameter("status", "Post status", "Jacky Music###Post to Discord");
        }

        protected override void RunCommand(String actionParameter) => _ = this.RunAsync(actionParameter);

        private async Task RunAsync(String actionParameter)
        {
            try
            {
                var result = await this.JackyPlugin.Client().AnnounceAsync(actionParameter).ConfigureAwait(false);
                // 200 with ok:false: the server answered the person at the
                // console ("Queue is empty") — show the detail on the key.
                this.SetLabel(actionParameter, result.Ok ? "✓" : (result.Detail ?? "Failed"));
            }
            catch (ControlApiException ex) when (ex.Status == JustPostedStatus)
            {
                // Not a failure to render as one: the post the user wanted is
                // already in the channel.
                this.SetLabel(actionParameter, "Just posted");
            }
            catch (Exception ex)
            {
                // Status codes and type names only — never bodies or tokens.
                PluginLog.Info($"announce failed: {(ex as ControlApiException)?.Status.ToString() ?? ex.GetType().Name}");
                // The Stream Deck key alerts on every failure; a silent key
                // reads as "it posted", so this one must answer too.
                this.SetLabel(actionParameter, "Failed");
            }
        }

        private void SetLabel(String actionParameter, String label)
            => this._labels.Set(actionParameter, label, () => this.ActionImageChanged(actionParameter));

        protected override BitmapImage GetCommandImage(String actionParameter, PluginImageSize imageSize)
        {
            var text = this._labels.Current(actionParameter)
                ?? (ShortNames.TryGetValue(actionParameter ?? "", out var name) ? name : "Post");

            using var bmp = new BitmapBuilder(imageSize);
            bmp.Clear(new BitmapColor(26, 26, 46));      // #1a1a2e, the family idiom
            bmp.DrawText(text, new BitmapColor(233, 69, 96), fontSize: 12); // #e94560
            return bmp.ToImage();
        }
    }
}
