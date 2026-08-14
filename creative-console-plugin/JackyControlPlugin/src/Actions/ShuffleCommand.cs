namespace Loupedeck.JackyControlPlugin
{
    using System;
    using System.Threading.Tasks;

    public class ShuffleCommand : PluginDynamicCommand
    {
        private JackyControlPlugin JackyPlugin => (JackyControlPlugin)this.Plugin;

        public ShuffleCommand() : base("Shuffle", "Shuffle the queue", "Jacky Music") { }

        protected override void RunCommand(String actionParameter) => _ = this.RunAsync();

        private async Task RunAsync()
        {
            try
            {
                await this.JackyPlugin.Client().ShuffleAsync().ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                // Status codes and type names only — never bodies or tokens.
                PluginLog.Info($"shuffle failed: {(ex as ControlApiException)?.Status.ToString() ?? ex.GetType().Name}");
            }
        }
    }
}
