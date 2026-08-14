namespace Loupedeck.JackyControlPlugin
{
    using System;
    using System.Threading.Tasks;

    /// <summary>One command, two parameters: keys for volume up and down.</summary>
    public class VolumeButtonsCommand : PluginDynamicCommand
    {
        private JackyControlPlugin JackyPlugin => (JackyControlPlugin)this.Plugin;

        public VolumeButtonsCommand() : base()
        {
            this.AddParameter("up", "Volume +5", "Jacky Music");
            this.AddParameter("down", "Volume −5", "Jacky Music");
        }

        protected override void RunCommand(String actionParameter)
            => _ = this.SendAsync(actionParameter == "up" ? 5 : -5);

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
    }
}
