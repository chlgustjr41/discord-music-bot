namespace Loupedeck.JackyControlPlugin
{
    using System;
    using System.Threading.Tasks;

    /// <summary>
    /// Queue a saved playlist into the active session. Guild and playlist come
    /// from editor listboxes fed by /control/playlists; the playlist list
    /// refreshes whenever the guild selection changes. Success answers with
    /// "+N" — how many tracks were inserted.
    /// </summary>
    public class PlayPlaylistCommand : ActionEditorCommand
    {
        private const String GuildControl = "guild";
        private const String PlaylistControl = "playlist";

        /// <summary>No active session to queue into — the fix is Summon, not retry.</summary>
        private const Int32 NoSessionStatus = 409;

        private JackyControlPlugin JackyPlugin => (JackyControlPlugin)this.Plugin;

        private readonly TransientLabels _labels = new();

        public PlayPlaylistCommand()
        {
            this.DisplayName = "Play Playlist";
            this.Description = "Queue a saved playlist into the session";
            this.GroupName = "Jacky Music";

            this.ActionEditor.AddControlEx(new ActionEditorListbox(GuildControl, "Server").SetRequired());
            this.ActionEditor.AddControlEx(new ActionEditorListbox(PlaylistControl, "Playlist").SetRequired());
            this.ActionEditor.ListboxItemsRequested += this.OnListboxItemsRequested;
            this.ActionEditor.ControlValueChanged += this.OnControlValueChanged;
        }

        /// <summary>Sync-blocked like SummonCommand's handler: the editor wants
        /// items before the handler returns, and the client's 10 s per-call
        /// timeout bounds the wait. Failure leaves the list empty.</summary>
        private void OnListboxItemsRequested(Object sender, ActionEditorListboxItemsRequestedEventArgs e)
        {
            try
            {
                if (e.ControlName.EqualsNoCase(GuildControl))
                {
                    foreach (var g in this.JackyPlugin.Client().PlaylistsAsync().GetAwaiter().GetResult())
                    {
                        e.AddItem(g.GuildId, g.GuildName, null);
                    }
                }
                else if (e.ControlName.EqualsNoCase(PlaylistControl))
                {
                    var guildId = e.ActionEditorState.GetControlValue(GuildControl);
                    if (String.IsNullOrEmpty(guildId))
                    {
                        return;
                    }
                    var guild = this.JackyPlugin.Client().PlaylistsAsync().GetAwaiter().GetResult()
                        .Find(g => g.GuildId == guildId);
                    if (guild?.Playlists == null)
                    {
                        return;
                    }
                    foreach (var p in guild.Playlists)
                    {
                        // The playlist NAME is the run parameter — the API takes
                        // (guildId, playlistName), there is no playlist id.
                        e.AddItem(p.Name, p.Name, $"{p.TrackCount} tracks");
                    }
                }
            }
            catch (Exception ex)
            {
                PluginLog.Info($"playlist listbox failed: {(ex as ControlApiException)?.Status.ToString() ?? ex.GetType().Name}");
            }
        }

        private void OnControlValueChanged(Object sender, ActionEditorControlValueChangedEventArgs e)
        {
            if (e.ControlName.EqualsNoCase(GuildControl))
            {
                this.ActionEditor.ListboxItemsChanged(PlaylistControl);
            }
        }

        protected override Boolean RunCommand(ActionEditorActionParameters parameters)
        {
            if (!parameters.TryGetString(GuildControl, out var guildId) || String.IsNullOrEmpty(guildId) ||
                !parameters.TryGetString(PlaylistControl, out var playlistName) || String.IsNullOrEmpty(playlistName))
            {
                return false;
            }
            _ = this.RunAsync(LabelKey(parameters), guildId, playlistName);
            return true;
        }

        private async Task RunAsync(String key, String guildId, String playlistName)
        {
            try
            {
                var result = await this.JackyPlugin.Client().PlayPlaylistAsync(guildId, playlistName).ConfigureAwait(false);
                this.SetLabel(key, "+" + result.Inserted);
            }
            catch (ControlApiException ex)
            {
                PluginLog.Info($"playlist failed: {ex.Status}");
                this.SetLabel(key, ex.Status switch
                {
                    401 => "Sign in",
                    NoSessionStatus => "No session",
                    _ => "Failed",
                });
            }
            catch (Exception ex)
            {
                PluginLog.Info($"playlist failed: {ex.GetType().Name}");
                this.SetLabel(key, "Failed");
            }
        }

        private static String LabelKey(ActionEditorActionParameters parameters)
            => parameters.GetHashCode64().ToString();

        private void SetLabel(String key, String label)
            => this._labels.Set(key, label, () => this.ActionImageChanged());

        protected override BitmapImage GetCommandImage(ActionEditorActionParameters parameters, Int32 imageWidth, Int32 imageHeight)
        {
            using var bmp = new BitmapBuilder(imageWidth, imageHeight);
            bmp.Clear(new BitmapColor(26, 26, 46));                    // #1a1a2e
            var text = this._labels.Current(LabelKey(parameters)) ?? "Playlist";
            bmp.DrawText(text, new BitmapColor(233, 69, 96), fontSize: 11); // #e94560
            return bmp.ToImage();
        }
    }
}
