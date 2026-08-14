namespace Loupedeck.JackyControlPlugin
{
    using System;
    using System.Diagnostics;
    using System.Threading.Tasks;

    /// <summary>
    /// Opens the web dashboard. The URL is server-supplied, so it goes through
    /// UrlGuard and ONLY the guard's returned string is handed to the shell —
    /// the checked string is the opened string.
    /// </summary>
    public class DashboardCommand : PluginDynamicCommand
    {
        private JackyControlPlugin JackyPlugin => (JackyControlPlugin)this.Plugin;

        public DashboardCommand() : base("Dashboard", "Open the web dashboard", "Jacky Music") { }

        protected override void RunCommand(String actionParameter) => _ = this.RunAsync();

        private async Task RunAsync()
        {
            try
            {
                var result = await this.JackyPlugin.Client().DashboardUrlAsync().ConfigureAwait(false);
                var safe = UrlGuard.OpenableUrl(result.Url);
                if (safe == null)
                {
                    // Guard rejected the server-supplied URL: nothing is opened.
                    PluginLog.Info("dashboard url rejected by guard");
                    return;
                }
                Process.Start(new ProcessStartInfo(safe) { UseShellExecute = true });
            }
            catch (Exception ex)
            {
                // Status codes and type names only — never bodies or tokens.
                PluginLog.Info($"dashboard failed: {(ex as ControlApiException)?.Status.ToString() ?? ex.GetType().Name}");
            }
        }
    }
}
