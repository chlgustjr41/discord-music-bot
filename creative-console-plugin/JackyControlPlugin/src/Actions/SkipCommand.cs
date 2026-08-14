namespace Loupedeck.JackyControlPlugin
{
    using System;
    using System.Threading.Tasks;

    public class SkipCommand : PluginDynamicCommand
    {
        private JackyControlPlugin JackyPlugin => (JackyControlPlugin)this.Plugin;

        public SkipCommand() : base("Skip", "Skip to the next track", "Jacky Music") { }

        protected override void RunCommand(String actionParameter) => _ = this.RunAsync();

        private async Task RunAsync()
        {
            try
            {
                await this.JackyPlugin.Client().SkipAsync().ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                // Status codes and type names only — never bodies or tokens.
                PluginLog.Info($"skip failed: {(ex as ControlApiException)?.Status.ToString() ?? ex.GetType().Name}");
            }
        }
    }
}
