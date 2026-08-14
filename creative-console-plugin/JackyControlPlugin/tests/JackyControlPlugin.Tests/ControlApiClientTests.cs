namespace Loupedeck.JackyControlPlugin.Tests;

using System.Net;
using System.Text;

using Loupedeck.JackyControlPlugin;

public class ControlApiClientTests
{
    private static (ControlApiClient client, List<HttpRequestMessage> seen, Func<string?> lastBody)
        Rig(HttpStatusCode status = HttpStatusCode.OK, string json = "{}")
    {
        var seen = new List<HttpRequestMessage>();
        string? lastBody = null;
        var handler = new FakeHandler(req =>
        {
            seen.Add(req);
            lastBody = req.Content?.ReadAsStringAsync().GetAwaiter().GetResult();
            return new HttpResponseMessage(status) { Content = new StringContent(json, Encoding.UTF8, "application/json") };
        });
        var client = new ControlApiClient(new HttpClient(handler), "https://api.test/", "tok123");
        return (client, seen, () => lastBody);
    }

    [Fact]
    public async Task posts_play_pause_with_bearer_and_normalized_url()
    {
        var (client, seen, _) = Rig();
        await client.PlayPauseAsync();
        Assert.Equal("https://api.test/control/play-pause", seen[0].RequestUri!.ToString());
        Assert.Equal(HttpMethod.Post, seen[0].Method);
        Assert.Equal("Bearer tok123", seen[0].Headers.Authorization!.ToString());
    }

    [Fact]
    public async Task non_2xx_throws_with_status()
    {
        var (client, _, _) = Rig(HttpStatusCode.Unauthorized);
        var ex = await Assert.ThrowsAsync<ControlApiException>(() => client.SkipAsync());
        Assert.Equal(401, ex.Status);
    }

    [Fact]
    public async Task now_playing_decodes_active_payload()
    {
        var (client, _, _) = Rig(json: """{"active":true,"title":"Song","author":"Artist","paused":false,"volume":70,"guildName":"G","thumbnail":"https://i.ytimg.com/vi/x/mqdefault.jpg"}""");
        var np = await client.NowPlayingAsync();
        Assert.True(np.Active);
        Assert.Equal("Song", np.Title);
        Assert.Equal(70, np.Volume);
    }

    [Fact]
    public async Task now_playing_decodes_inactive_payload()
    {
        var (client, _, _) = Rig(json: """{"active":false}""");
        var np = await client.NowPlayingAsync();
        Assert.False(np.Active);
        Assert.Null(np.Title);
    }

    [Fact]
    public async Task volume_posts_delta_body()
    {
        var (client, _, body) = Rig();
        await client.VolumeAsync(-5);
        Assert.Contains("\"delta\":-5", body());
    }

    [Fact]
    public async Task announce_posts_command_and_decodes_result()
    {
        var (client, _, body) = Rig(json: """{"ok":false,"detail":"Queue is empty"}""");
        var result = await client.AnnounceAsync("queue");
        Assert.Contains("\"command\":\"queue\"", body());
        Assert.False(result.Ok);
        Assert.Equal("Queue is empty", result.Detail);
    }

    [Fact]
    public async Task voice_sends_wav_with_language_and_debug_only_when_set()
    {
        var (client, seen, _) = Rig(json: """{"transcript":"t","actions":[],"ok":true,"detail":null}""");
        await client.VoiceCommandAsync(new byte[] { 1 }, language: null, debug: false);
        Assert.Equal("https://api.test/control/voice", seen[0].RequestUri!.ToString());
        Assert.Equal("audio/wav", seen[0].Content!.Headers.ContentType!.MediaType);
        await client.VoiceCommandAsync(new byte[] { 1 }, language: "ko", debug: true);
        Assert.Equal("https://api.test/control/voice?language=ko&debug=1", seen[1].RequestUri!.ToString());
    }

    [Fact]
    public async Task voice_language_only_appends_single_query_param()
    {
        var (client, seen, _) = Rig(json: """{"transcript":"t","actions":[],"ok":true,"detail":null}""");
        await client.VoiceCommandAsync(new byte[] { 1 }, language: "en", debug: false);
        Assert.Equal("https://api.test/control/voice?language=en", seen[0].RequestUri!.ToString());
        await client.VoiceCommandAsync(new byte[] { 1 }, language: "", debug: true);
        Assert.Equal("https://api.test/control/voice?debug=1", seen[1].RequestUri!.ToString());
    }

    [Fact]
    public async Task voice_decodes_client_directives()
    {
        var (client, _, _) = Rig(json: """{"transcript":"t","actions":[{"action":"open_dashboard","ok":true,"detail":"d"}],"ok":true,"detail":"done","client":[{"type":"open_url","url":"https://x.test/d"}]}""");
        var result = await client.VoiceCommandAsync(new byte[] { 1 }, null, false);
        Assert.Single(result.Client);
        Assert.Equal("open_url", result.Client[0].Type);
    }

    [Fact]
    public async Task summon_posts_ids_and_decodes_result()
    {
        var (client, seen, body) = Rig(json: """{"action":"joined","sessionCode":"ABCD"}""");
        var result = await client.SummonAsync("g1", "c1");
        Assert.Equal("https://api.test/control/summon", seen[0].RequestUri!.ToString());
        Assert.Equal(HttpMethod.Post, seen[0].Method);
        Assert.Contains("\"guildId\":\"g1\"", body());
        Assert.Contains("\"channelId\":\"c1\"", body());
        Assert.Equal("joined", result.Action);
        Assert.Equal("ABCD", result.SessionCode);
    }

    [Fact]
    public async Task channels_decodes_array()
    {
        var (client, seen, _) = Rig(json: """[{"guildId":"g1","guildName":"Guild One","channels":[{"id":"c1","name":"General"},{"id":"c2","name":"Music"}]}]""");
        var list = await client.ChannelsAsync();
        Assert.Equal("https://api.test/control/channels", seen[0].RequestUri!.ToString());
        Assert.Equal(HttpMethod.Get, seen[0].Method);
        Assert.Single(list);
        Assert.Equal("Guild One", list[0].GuildName);
        Assert.Equal(2, list[0].Channels.Count);
        Assert.Equal("Music", list[0].Channels[1].Name);
    }

    [Fact]
    public async Task playlists_decodes_array()
    {
        var (client, seen, _) = Rig(json: """[{"guildId":"g1","guildName":"Guild One","playlists":[{"name":"Chill","trackCount":12}]}]""");
        var list = await client.PlaylistsAsync();
        Assert.Equal("https://api.test/control/playlists", seen[0].RequestUri!.ToString());
        Assert.Equal(HttpMethod.Get, seen[0].Method);
        Assert.Single(list);
        Assert.Equal("Chill", list[0].Playlists[0].Name);
        Assert.Equal(12, list[0].Playlists[0].TrackCount);
    }

    [Fact]
    public async Task play_playlist_posts_both_fields_and_decodes_inserted()
    {
        var (client, seen, body) = Rig(json: """{"inserted":7,"playlistName":"Chill"}""");
        var result = await client.PlayPlaylistAsync("g1", "Chill");
        Assert.Equal("https://api.test/control/playlist", seen[0].RequestUri!.ToString());
        Assert.Contains("\"guildId\":\"g1\"", body());
        Assert.Contains("\"playlistName\":\"Chill\"", body());
        Assert.Equal(7, result.Inserted);
        Assert.Equal("Chill", result.PlaylistName);
    }

    [Fact]
    public async Task dashboard_url_decodes()
    {
        var (client, seen, _) = Rig(json: """{"active":true,"url":"https://music.test/dashboard?g=1","guildName":"G"}""");
        var result = await client.DashboardUrlAsync();
        Assert.Equal("https://api.test/control/dashboard-url", seen[0].RequestUri!.ToString());
        Assert.Equal(HttpMethod.Get, seen[0].Method);
        Assert.True(result.Active);
        Assert.Equal("https://music.test/dashboard?g=1", result.Url);
    }

    [Fact]
    public async Task stop_posts_route()
    {
        var (client, seen, _) = Rig();
        await client.StopAsync();
        Assert.Equal("https://api.test/control/stop", seen[0].RequestUri!.ToString());
        Assert.Equal(HttpMethod.Post, seen[0].Method);
    }

    [Fact]
    public async Task shuffle_posts_route()
    {
        var (client, seen, _) = Rig();
        await client.ShuffleAsync();
        Assert.Equal("https://api.test/control/shuffle", seen[0].RequestUri!.ToString());
        Assert.Equal(HttpMethod.Post, seen[0].Method);
    }
}
