namespace Loupedeck.JackyControlPlugin
{
    using System;
    using System.Diagnostics;
    using System.Threading;
    using System.Threading.Tasks;

    /// <summary>
    /// Discord OAuth sign-in. Mirrors the Stream Deck property inspector's
    /// sign-in button: every press (re-)runs the flow, signed in or not —
    /// re-signing simply mints a fresh token for this device. AuthFlow guards
    /// the authorize URL; the string handed to the shell is exactly the
    /// guard's return.
    /// </summary>
    public class SignInCommand : ActionEditorCommand
    {
        private const String ServerUrlControl = "serverUrl";
        private const String DefaultUrl = "https://control.jacky-music-bot.com";

        private JackyControlPlugin JackyPlugin => (JackyControlPlugin)this.Plugin;

        /// <summary>1 while a flow is in flight. A sign-in opens a browser tab
        /// and polls for up to five minutes, so a second press must not stack
        /// a second flow (and a second tab) on top of the first.</summary>
        private Int32 _signingIn;

        public SignInCommand()
        {
            this.DisplayName = "Sign In";
            this.Description = "Sign in to Jacky Music with Discord";
            this.GroupName = "Jacky Music";

            this.ActionEditor.AddControlEx(
                new ActionEditorTextbox(ServerUrlControl, "Server URL",
                        $"Leave empty for the default ({DefaultUrl})")
                    .SetPlaceholder(DefaultUrl));
        }

        protected override Boolean RunCommand(ActionEditorActionParameters parameters)
        {
            parameters.TryGetString(ServerUrlControl, out var configured);
            var url = String.IsNullOrWhiteSpace(configured) ? this.JackyPlugin.ApiUrl : configured.Trim();

            if (Interlocked.CompareExchange(ref this._signingIn, 1, 0) != 0)
            {
                PluginLog.Info("sign-in already in flight; press ignored");
                return true;
            }
            _ = this.SignInAsync(url);
            return true;
        }

        private async Task SignInAsync(String url)
        {
            try
            {
                var result = await new AuthFlow(this.JackyPlugin.Http)
                    .SignInAsync(url, safe => Process.Start(new ProcessStartInfo(safe) { UseShellExecute = true }))
                    .ConfigureAwait(false);
                // The token is persisted, never logged.
                this.JackyPlugin.SaveSignIn(url, result.Token);
            }
            catch (SignInException ex)
            {
                // Status and the server's error code only — never the token.
                PluginLog.Info($"sign-in failed: {ex.Status}:{ex.Code}");
            }
            catch (Exception ex)
            {
                PluginLog.Info($"sign-in failed: {ex.GetType().Name}");
            }
            finally
            {
                Interlocked.Exchange(ref this._signingIn, 0);
                this.ActionImageChanged();
            }
        }

        protected override BitmapImage GetCommandImage(ActionEditorActionParameters parameters, Int32 imageWidth, Int32 imageHeight)
        {
            using var bmp = new BitmapBuilder(imageWidth, imageHeight);
            bmp.Clear(new BitmapColor(26, 26, 46));                    // #1a1a2e, the family idiom
            if (this.JackyPlugin.SignedIn)
            {
                bmp.DrawText("✓ Signed in", new BitmapColor(74, 222, 128), fontSize: 11);  // #4ade80
            }
            else
            {
                bmp.DrawText("Sign in", new BitmapColor(233, 69, 96), fontSize: 12);       // #e94560
            }
            return bmp.ToImage();
        }
    }
}
