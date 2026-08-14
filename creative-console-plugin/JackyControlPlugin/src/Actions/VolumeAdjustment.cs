namespace Loupedeck.JackyControlPlugin
{
    using System;
    using System.Threading.Tasks;

    /// <summary>
    /// Volume dial: one tick = 5 volume points. The value next to the dial is
    /// the last volume the shared poller saw, so it tracks changes made from
    /// Discord or the web app too, not just this dial.
    /// </summary>
    public class VolumeAdjustment : PluginDynamicAdjustment
    {
        private JackyControlPlugin JackyPlugin => (JackyControlPlugin)this.Plugin;

        /// <summary>Last volume seen by the poller; null while the state isn't Data.</summary>
        private Int32? _volume;

        public VolumeAdjustment() : base("Volume", "Adjust playback volume", "Jacky Music", hasReset: false) { }

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
            var volume = state is PollState.Data data ? data.NowPlaying.Volume : (Int32?)null;
            if (volume != this._volume)
            {
                this._volume = volume;
                this.AdjustmentValueChanged();
            }
        }

        protected override void ApplyAdjustment(String actionParameter, Int32 diff)
        {
            _ = this.SendAsync(diff * 5);
            this.AdjustmentValueChanged();
        }

        private async Task SendAsync(Int32 delta)
        {
            try
            {
                await this.JackyPlugin.Client().VolumeAsync(delta).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                // Status codes and type names only — never bodies or tokens.
                PluginLog.Info($"volume failed: {(ex as ControlApiException)?.Status.ToString() ?? ex.GetType().Name}");
            }
        }

        protected override String GetAdjustmentValue(String actionParameter)
            => this._volume?.ToString() ?? "—";
    }
}
