namespace Loupedeck.JackyControlPlugin
{
    using System;
    using System.Threading.Tasks;

    public class StopCommand : PluginDynamicCommand
    {
        private JackyControlPlugin JackyPlugin => (JackyControlPlugin)this.Plugin;

        public StopCommand() : base("Stop", "Stop playback and clear the player", "Jacky Music") { }

        protected override void RunCommand(String actionParameter) => _ = this.RunAsync();

        private async Task RunAsync()
        {
            try
            {
                await this.JackyPlugin.Client().StopAsync().ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                // Status codes and type names only — never bodies or tokens.
                PluginLog.Info($"stop failed: {(ex as ControlApiException)?.Status.ToString() ?? ex.GetType().Name}");
            }
        }
    }
}
