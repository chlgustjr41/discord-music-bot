namespace Loupedeck.JackyControlPlugin
{
    using System;
    using System.Threading.Tasks;

    /// <summary>
    /// Summon Jacky into a configured voice channel (press again to dismiss —
    /// the server toggles). Guild and channel come from editor listboxes fed
    /// by /control/channels; the channel list refreshes whenever the guild
    /// selection changes.
    /// </summary>
    public class SummonCommand : ActionEditorCommand
    {
        private const String GuildControl = "guild";
        private const String ChannelControl = "channel";

        private JackyControlPlugin JackyPlugin => (JackyControlPlugin)this.Plugin;

        private readonly TransientLabels _labels = new();

        public SummonCommand()
        {
            this.DisplayName = "Summon";
            this.Description = "Summon Jacky to a voice channel (press again to dismiss)";
            this.GroupName = "Jacky Music";

            this.ActionEditor.AddControlEx(new ActionEditorListbox(GuildControl, "Server").SetRequired());
            this.ActionEditor.AddControlEx(new ActionEditorListbox(ChannelControl, "Voice channel").SetRequired());
            this.ActionEditor.ListboxItemsRequested += this.OnListboxItemsRequested;
            this.ActionEditor.ControlValueChanged += this.OnControlValueChanged;
        }

        /// <summary>The editor requests items synchronously, so the API call is
        /// sync-blocked here; the client's own 10 s per-call timeout bounds it.
        /// On failure the list simply stays empty — status is logged, bodies
        /// are not.</summary>
        private void OnListboxItemsRequested(Object sender, ActionEditorListboxItemsRequestedEventArgs e)
        {
            try
            {
                if (e.ControlName.EqualsNoCase(GuildControl))
                {
                    foreach (var g in this.JackyPlugin.Client().ChannelsAsync().GetAwaiter().GetResult())
                    {
                        e.AddItem(g.GuildId, g.GuildName, null);
                    }
                }
                else if (e.ControlName.EqualsNoCase(ChannelControl))
                {
                    var guildId = e.ActionEditorState.GetControlValue(GuildControl);
                    if (String.IsNullOrEmpty(guildId))
                    {
                        return; // no guild picked yet — nothing to offer
                    }
                    var guild = this.JackyPlugin.Client().ChannelsAsync().GetAwaiter().GetResult()
                        .Find(g => g.GuildId == guildId);
                    if (guild?.Channels == null)
                    {
                        return;
                    }
                    foreach (var c in guild.Channels)
                    {
                        e.AddItem(c.Id, c.Name, null);
                    }
                }
            }
            catch (Exception ex)
            {
                PluginLog.Info($"summon listbox failed: {(ex as ControlApiException)?.Status.ToString() ?? ex.GetType().Name}");
            }
        }

        private void OnControlValueChanged(Object sender, ActionEditorControlValueChangedEventArgs e)
        {
            if (e.ControlName.EqualsNoCase(GuildControl))
            {
                // A new guild means a new channel list.
                this.ActionEditor.ListboxItemsChanged(ChannelControl);
            }
        }

        protected override Boolean RunCommand(ActionEditorActionParameters parameters)
        {
            if (!parameters.TryGetString(GuildControl, out var guildId) || String.IsNullOrEmpty(guildId) ||
                !parameters.TryGetString(ChannelControl, out var channelId) || String.IsNullOrEmpty(channelId))
            {
                return false;
            }
            _ = this.RunAsync(LabelKey(parameters), guildId, channelId);
            return true;
        }

        private async Task RunAsync(String key, String guildId, String channelId)
        {
            try
            {
                var result = await this.JackyPlugin.Client().SummonAsync(guildId, channelId).ConfigureAwait(false);
                // "left" is the toggle's other half, not a failure. On join the
                // session code (when the server minted one) is the thing the
                // person at the console wants to read out to the room.
                this.SetLabel(key, result.Action == "left"
                    ? "Left"
                    : "Joined" + (result.SessionCode != null ? " " + result.SessionCode : ""));
            }
            catch (ControlApiException ex)
            {
                // Summon CREATES the session, so there is no "No session" here;
                // 401 is the one status with a better instruction than "Failed".
                PluginLog.Info($"summon failed: {ex.Status}");
                this.SetLabel(key, ex.Status == 401 ? "Sign in" : "Failed");
            }
            catch (Exception ex)
            {
                PluginLog.Info($"summon failed: {ex.GetType().Name}");
                this.SetLabel(key, "Failed");
            }
        }

        /// <summary>Labels are keyed by the parameter set: two keys configured
        /// for different channels must not show each other's result.</summary>
        private static String LabelKey(ActionEditorActionParameters parameters)
            => parameters.GetHashCode64().ToString();

        private void SetLabel(String key, String label)
            => this._labels.Set(key, label, () => this.ActionImageChanged());

        protected override BitmapImage GetCommandImage(ActionEditorActionParameters parameters, Int32 imageWidth, Int32 imageHeight)
        {
            using var bmp = new BitmapBuilder(imageWidth, imageHeight);
            bmp.Clear(new BitmapColor(26, 26, 46));                    // #1a1a2e
            var text = this._labels.Current(LabelKey(parameters)) ?? "Summon";
            bmp.DrawText(text, new BitmapColor(233, 69, 96), fontSize: 11); // #e94560
            return bmp.ToImage();
        }
    }
}
