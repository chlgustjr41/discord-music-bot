namespace Loupedeck.JackyControlPlugin
{
    using System;
    using System.Net.Http;
    using System.Threading;

    /// <summary>
    /// Plugin-level wiring: the shared HttpClient, the shared session poller,
    /// and the settings-backed auth state every action reads through.
    /// </summary>
    public class JackyControlPlugin : Plugin
    {
        // Gets a value indicating whether this is an API-only plugin.
        public override Boolean UsesApplicationApiOnly => true;

        // Gets a value indicating whether this is a Universal plugin or an Application plugin.
        public override Boolean HasNoApplication => true;

        private const String DefaultApiUrl = "https://control.jacky-music-bot.com";
        private const String ApiUrlSetting = "apiUrl";
        private const String AuthTokenSetting = "authToken";

        /// <summary>
        /// Shared by every action and the poller. Infinite timeout is REQUIRED:
        /// ControlApiClient's per-call CancellationTokenSource is the single
        /// timeout authority (10 s ordinary routes, 30 s voice) — any finite
        /// value here would race it.
        /// </summary>
        public HttpClient Http { get; } = new() { Timeout = Timeout.InfiniteTimeSpan };

        public SessionPoller Poller { get; private set; }

        public String ApiUrl => this.TryGetPluginSetting(ApiUrlSetting, out var url) && !String.IsNullOrWhiteSpace(url) ? url : DefaultApiUrl;

        public String AuthToken => this.TryGetPluginSetting(AuthTokenSetting, out var token) ? token : null;

        public Boolean SignedIn => !String.IsNullOrEmpty(this.AuthToken);

        /// <summary>A client for the current settings. Cheap: state lives in HttpClient/settings.</summary>
        public ControlApiClient Client() => new(this.Http, this.ApiUrl, this.AuthToken);

        public void SaveSignIn(String apiUrl, String token)
        {
            this.SetPluginSetting(ApiUrlSetting, apiUrl);
            this.SetPluginSetting(AuthTokenSetting, token); // never logged
            this.Poller.Kick();
        }

        public void SignOut()
        {
            this.DeletePluginSetting(AuthTokenSetting);
            this.Poller.Kick();
        }

        // Initializes a new instance of the plugin class.
        public JackyControlPlugin()
        {
            // Initialize the plugin log.
            PluginLog.Init(this.Log);

            // Initialize the plugin resources.
            PluginResources.Init(this.Assembly);
        }

        // This method is called when the plugin is loaded.
        public override void Load()
            // A poll while signed out throws ControlApiException(0), which the
            // poller maps to Unconfigured — the Stream Deck plugin's convention.
            => this.Poller = new SessionPoller(() =>
                this.SignedIn ? this.Client().NowPlayingAsync() : throw new ControlApiException(0));

        // This method is called when the plugin is unloaded.
        public override void Unload() => this.Http.Dispose();
    }
}
