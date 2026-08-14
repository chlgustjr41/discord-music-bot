namespace Loupedeck.JackyControlPlugin;

using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

public sealed class ControlApiException : Exception
{
    public Int32 Status { get; }
    public ControlApiException(Int32 status) : base($"control api responded {status}") => this.Status = status;
}

public sealed record NowPlaying(Boolean Active, String Title, String Author, Boolean Paused, Int32 Volume, String GuildName, String Thumbnail);
public sealed record GuildChannels(String GuildId, String GuildName, List<NamedId> Channels);
public sealed record NamedId(String Id, String Name);
public sealed record GuildPlaylists(String GuildId, String GuildName, List<PlaylistInfo> Playlists);
public sealed record PlaylistInfo(String Name, Int32 TrackCount);
public sealed record DashboardUrl(Boolean Active, String Url, String GuildName);
public sealed record SummonResult(String Action, String SessionCode);
public sealed record AnnounceResult(Boolean Ok, String Detail);
public sealed record VoiceAction(String Action, Boolean Ok, String Detail);
public sealed record ClientDirective(String Type, String Url);
public sealed record VoiceResult(String Transcript, List<VoiceAction> Actions, Boolean Ok, String Detail, List<ClientDirective> Client);
public sealed record PlayPlaylistResult(Int32 Inserted, String PlaylistName);

/// <summary>
/// Port of api-client.ts: bearer-authed typed client over /control/*.
/// Timeouts: every request carries its own per-call CancellationTokenSource —
/// 10 s for the ordinary routes, 30 s for /control/voice (that request includes
/// a transcription round-trip). The per-call CTS is the single timeout
/// authority, so the Plugin (Task 6) MUST construct the shared HttpClient with
/// <c>Timeout = Timeout.InfiniteTimeSpan</c>; otherwise HttpClient's own 100 s
/// default (or any shorter value, e.g. 10 s) would race the voice call's 30 s.
/// </summary>
public sealed class ControlApiClient
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNameCaseInsensitive = true };
    private static readonly TimeSpan DefaultTimeout = TimeSpan.FromSeconds(10);
    private static readonly TimeSpan VoiceTimeout = TimeSpan.FromSeconds(30);

    private readonly HttpClient _http;
    private readonly String _baseUrl;
    private readonly String _token;

    public ControlApiClient(HttpClient http, String apiUrl, String authToken)
    {
        this._http = http;
        this._baseUrl = (apiUrl ?? "").TrimEnd('/');
        this._token = authToken;
    }

    private HttpRequestMessage Request(HttpMethod method, String path)
    {
        var req = new HttpRequestMessage(method, this._baseUrl + path);
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", this._token);
        return req;
    }

    private async Task<String> SendAsync(HttpRequestMessage req, TimeSpan timeout)
    {
        using (req)
        {
            using var cts = new CancellationTokenSource(timeout);
            // SendAsync defaults to ResponseContentRead, so the body is fully
            // buffered before the CTS goes out of scope.
            using var res = await this._http.SendAsync(req, cts.Token).ConfigureAwait(false);
            if (!res.IsSuccessStatusCode)
            {
                throw new ControlApiException((Int32)res.StatusCode);
            }
            return await res.Content.ReadAsStringAsync(cts.Token).ConfigureAwait(false);
        }
    }

    private Task<String> PostAsync(String path, Object body = null)
    {
        var req = this.Request(HttpMethod.Post, path);
        req.Content = new StringContent(body == null ? "{}" : JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");
        return this.SendAsync(req, DefaultTimeout);
    }

    private Task<String> GetAsync(String path) => this.SendAsync(this.Request(HttpMethod.Get, path), DefaultTimeout);

    public Task PlayPauseAsync() => this.PostAsync("/control/play-pause");
    public Task SkipAsync() => this.PostAsync("/control/skip");
    public Task StopAsync() => this.PostAsync("/control/stop");
    public Task ShuffleAsync() => this.PostAsync("/control/shuffle");
    public Task VolumeAsync(Int32 delta) => this.PostAsync("/control/volume", new { delta });

    public async Task<NowPlaying> NowPlayingAsync() => JsonSerializer.Deserialize<NowPlaying>(await this.GetAsync("/control/now-playing").ConfigureAwait(false), Json);
    public async Task<List<GuildChannels>> ChannelsAsync() => JsonSerializer.Deserialize<List<GuildChannels>>(await this.GetAsync("/control/channels").ConfigureAwait(false), Json);
    public async Task<List<GuildPlaylists>> PlaylistsAsync() => JsonSerializer.Deserialize<List<GuildPlaylists>>(await this.GetAsync("/control/playlists").ConfigureAwait(false), Json);
    public async Task<DashboardUrl> DashboardUrlAsync() => JsonSerializer.Deserialize<DashboardUrl>(await this.GetAsync("/control/dashboard-url").ConfigureAwait(false), Json);
    public async Task<SummonResult> SummonAsync(String guildId, String channelId) => JsonSerializer.Deserialize<SummonResult>(await this.PostAsync("/control/summon", new { guildId, channelId }).ConfigureAwait(false), Json);
    public async Task<AnnounceResult> AnnounceAsync(String command) => JsonSerializer.Deserialize<AnnounceResult>(await this.PostAsync("/control/announce", new { command }).ConfigureAwait(false), Json);

    public async Task<PlayPlaylistResult> PlayPlaylistAsync(String guildId, String playlistName)
        => JsonSerializer.Deserialize<PlayPlaylistResult>(await this.PostAsync("/control/playlist", new { guildId, playlistName }).ConfigureAwait(false), Json);

    /// <summary>
    /// POST /control/voice with a raw WAV body. `language` fixes the
    /// transcription language and is OMITTED when unset (the server's default
    /// is English, and `?language=` would be a value it has to normalize
    /// away); `debug` is only ever sent when ON — absent already means off
    /// server-side, so there is no `debug=0` to send. 30 s budget, not the
    /// usual 10 s: this request includes a transcription round-trip.
    /// </summary>
    public async Task<VoiceResult> VoiceCommandAsync(Byte[] wav, String language, Boolean debug)
    {
        var query = "";
        if (!String.IsNullOrEmpty(language))
        {
            query = "?language=" + Uri.EscapeDataString(language);
        }
        if (debug)
        {
            query += (query.Length == 0 ? "?" : "&") + "debug=1";
        }
        var req = this.Request(HttpMethod.Post, "/control/voice" + query);
        req.Content = new ByteArrayContent(wav);
        req.Content.Headers.ContentType = new MediaTypeHeaderValue("audio/wav");
        return JsonSerializer.Deserialize<VoiceResult>(await this.SendAsync(req, VoiceTimeout).ConfigureAwait(false), Json);
    }
}
