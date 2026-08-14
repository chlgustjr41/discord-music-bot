# Creative Console Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Logi Actions (MX Creative Console) plugin mirroring the Stream Deck plugin's feature set against the bot's existing `/control/*` API — zero bot changes.

**Architecture:** C# net8.0 `Plugin` subclass owning shared services (HttpClient-based `ControlApiClient`, `SessionPoller`, settings-backed auth state); one file per action under `src/Actions/`; pure logic (URL guard, thumbnail rewrite, title truncation, poller state machine) isolated from the SDK so xUnit covers it without a device. Spec: `docs/superpowers/specs/2026-08-14-creative-console-plugin-design.md`.

**Tech Stack:** Logi Actions SDK (PluginApi.dll), System.Text.Json, NAudio (voice capture), xUnit + Moq-free hand-rolled fakes.

---

## Environment facts (read first)

- Working dir: `creative-console-plugin/` on branch `feat/creative-console`. The generated solution is `JackyControlPlugin/JackyControlPlugin.sln`; the plugin project is `JackyControlPlugin/src/JackyControlPlugin.csproj`, namespace **`Loupedeck.JackyControlPlugin`**.
- **PluginApi.dll**: this machine has no Logi Plugin Service. `creative-console-plugin/fetch-pluginapi.ps1` copies PluginApi from the LogiPluginTool dotnet-tool install into untracked `creative-console-plugin/sdk/`, and the csproj falls back to it automatically. If a build says PluginApi is missing, run the script; **never commit `sdk/`**.
- `dotnet build` on the skeleton already succeeds (verified). PostBuild steps that talk to the (absent) plugin service are `ContinueOnError` — ignore their warnings.
- **Verified API surface** (reflected from PluginApi.dll — use these, do not invent members):
  - `Plugin`: `TryGetPluginSetting(string, out string)`, `SetPluginSetting(string, string)`, `DeletePluginSetting(string)`, `Load()`, `Unload()`, `OnApplicationConnected/Disconnected` (virtuals).
  - `PluginDynamicCommand` (base `PluginDynamicAction`): ctor `(string displayName, string description, string groupName)`, `RunCommand(string actionParameter)` override, `GetCommandImage(string actionParameter, PluginImageSize)` → `BitmapImage`, `GetCommandDisplayName(string, PluginImageSize)`, `AddParameter(string actionParameter, string displayName, string groupName)`, `ActionImageChanged()` / `ActionImageChanged(string actionParameter)`.
  - `PluginDynamicAdjustment`: ctor `(string displayName, string description, string groupName, bool hasReset)`, `ApplyAdjustment(string actionParameter, int diff)` override, `GetAdjustmentValue(string actionParameter)` → string, `AdjustmentValueChanged()`.
  - `ActionEditorCommand`: `RunCommand(ActionEditorActionParameters)` → bool override, `GetCommandImage(ActionEditorActionParameters, int width, int height)`, `ActionImageChanged()`, `this.ActionEditor.AddControlEx(...)`, events `ListboxItemsRequested`, `ControlValueChanged`; `this.ActionEditor.ListboxItemsChanged(controlName)` to refresh a listbox. Controls: `ActionEditorTextbox(name, label)`, `ActionEditorCheckbox(name, label)`, `ActionEditorListbox(name, label)`; params via `e.ActionEditorState.GetControlValue(name)` / on run `parameters.TryGetString(name, out var v)`.
  - `BitmapBuilder` (ctor from `PluginImageSize` via `new BitmapBuilder(imageSize.GetWidth(), imageSize.GetHeight())` or `(int, int)`): `Clear(BitmapColor)`, `FillRectangle`, `DrawText(string, int x, int y, int w, int h, BitmapColor?, int fontSize, ...)`, `DrawText(string, BitmapColor?, int fontSize, ...)`, `DrawImage(BitmapImage, int x, int y)`, `ToImage()`.
  - `BitmapImage.FromArray(byte[])`, `TryCreateFromArray(byte[], out BitmapImage)`, `BitmapColor(byte r, byte g, byte b)`.
- Reference ports (read them before porting — semantics must match exactly): `streamdeck-plugin/src/url-guard.ts`, `thumbnail.ts`, `api-client.ts`, `auth.ts`, `poller.ts`, and `streamdeck-plugin/src/actions/*.ts` for per-action behaviour.
- House rules: TDD; **mutation-verify each guard** (back the file up by COPY to the scratchpad, break the guard, watch the test fail, restore the copy — never `git checkout --`); plain `git commit` (a hook rejects `--no-verify`); Windows paths in commands below assume repo root `D:\web-project\discord-music-bot`.
- Run tests with `dotnet test creative-console-plugin/JackyControlPlugin/JackyControlPlugin.sln`.

## File structure (final)

```
creative-console-plugin/
  fetch-pluginapi.ps1                      (exists)
  JackyControlPlugin/
    JackyControlPlugin.sln                 (gains the test project)
    src/                                   JackyControlPlugin.csproj (net8.0)
      JackyControlPlugin.cs                Plugin subclass + shared services
      Core/UrlGuard.cs                     https-only opened-URL guard (pure)
      Core/Thumbnails.cs                   yt thumbnail rewrite + capped fetch
      Core/ControlApiClient.cs             typed /control client + ControlApiException
      Core/AuthFlow.cs                     OAuth start → open browser → poll
      Core/SessionPoller.cs                shared now-playing loop, 5s→30s backoff
      Core/KeyText.cs                      two-line title truncation (pure)
      Audio/MicRecorder.cs                 NAudio 16k mono 16-bit WAV in memory
      Actions/PlayPauseCommand.cs          live image: artwork/glyph + title + ⏸
      Actions/SkipCommand.cs
      Actions/StopCommand.cs
      Actions/ShuffleCommand.cs
      Actions/VolumeButtonsCommand.cs      parameterised +5 / −5
      Actions/VolumeAdjustment.cs          dial, diff×5, value = current volume
      Actions/AnnounceCommand.cs           parameterised: session/nowplaying/queue/status
      Actions/DashboardCommand.cs
      Actions/SignInCommand.cs             ActionEditor: server URL textbox
      Actions/SummonCommand.cs             ActionEditor: guild+channel listboxes
      Actions/PlayPlaylistCommand.cs       ActionEditor: guild+playlist listboxes
      Actions/VoiceCommand.cs              ActionEditor: mic/language/debug; press-to-toggle
      (delete Actions/CounterCommand.cs, Actions/CounterAdjustment.cs)
    tests/JackyControlPlugin.Tests/        xUnit, references src project
      UrlGuardTests.cs  ThumbnailsTests.cs  ControlApiClientTests.cs
      AuthFlowTests.cs  SessionPollerTests.cs  KeyTextTests.cs
docs/creative-console-control.md           setup + behaviour + on-device checklist
```

Design decisions locked by the spec: press-to-toggle voice (press events only), truncated two-line title (no marquee), volume dial + buttons, one parameterised Announce command, https-only guard where **the checked string is the opened string**, token in plugin settings never logged, transcripts never logged.

---

### Task 1: Test project + UrlGuard

**Files:**
- Create: `creative-console-plugin/JackyControlPlugin/tests/JackyControlPlugin.Tests/JackyControlPlugin.Tests.csproj`
- Create: `creative-console-plugin/JackyControlPlugin/tests/JackyControlPlugin.Tests/UrlGuardTests.cs`
- Create: `creative-console-plugin/JackyControlPlugin/src/Core/UrlGuard.cs`
- Modify: `creative-console-plugin/JackyControlPlugin/JackyControlPlugin.sln` (add test project via `dotnet sln add`)

- [ ] **Step 1: Create the test project**

```bash
cd creative-console-plugin/JackyControlPlugin
dotnet new xunit -o tests/JackyControlPlugin.Tests
dotnet add tests/JackyControlPlugin.Tests reference src/JackyControlPlugin.csproj
dotnet sln add tests/JackyControlPlugin.Tests
```

Then edit `tests/JackyControlPlugin.Tests/JackyControlPlugin.Tests.csproj`: ensure `<TargetFramework>net8.0</TargetFramework>` and add inside the first `<PropertyGroup>`:

```xml
<AppendTargetFrameworkToOutputPath>false</AppendTargetFrameworkToOutputPath>
```

is NOT needed — leave defaults except the reference. The src csproj's `CopyLocalLockFileAssemblies` already copies PluginApi.dll next to the test binaries.

- [ ] **Step 2: Write the failing UrlGuard tests** — the Stream Deck matrix ported:

```csharp
namespace Loupedeck.JackyControlPlugin.Tests;

using Loupedeck.JackyControlPlugin;

public class UrlGuardTests
{
    [Theory]
    [InlineData("https://music.example/dashboard?g=1", "https://music.example/dashboard?g=1")]
    // WHATWG-ish normalization: the returned string is what gets opened, not the input.
    [InlineData(" https://music.example/d ", "https://music.example/d")]
    [InlineData("https://MUSIC.example/d", "https://music.example/d")]
    public void allows_https_and_returns_normalized(string input, string expected)
        => Assert.Equal(expected, UrlGuard.OpenableUrl(input));

    [Theory]
    [InlineData("http://music.example/dashboard")]
    [InlineData("javascript:alert(1)")]
    [InlineData("file:///C:/Windows/notepad.exe")]
    [InlineData("data:text/html,hi")]
    [InlineData("customscheme://payload")]
    [InlineData("not a url")]
    [InlineData("")]
    public void rejects_everything_else(string input)
        => Assert.Null(UrlGuard.OpenableUrl(input));
}
```

- [ ] **Step 3: Run and verify failure** — `dotnet test creative-console-plugin/JackyControlPlugin/JackyControlPlugin.sln` → compile error: `UrlGuard` not defined.

- [ ] **Step 4: Implement `src/Core/UrlGuard.cs`**

```csharp
namespace Loupedeck.JackyControlPlugin;

using System;

/// <summary>
/// The URL that is safe to hand to the OS shell, or null. Port of the
/// Stream Deck plugin's url-guard.ts: https-only with a non-empty host,
/// and the caller must open the RETURNED string — Uri normalizes
/// (trims, lowercases host), so the checked string is the opened string.
/// </summary>
public static class UrlGuard
{
    public static String OpenableUrl(String url)
    {
        if (!Uri.TryCreate(url?.Trim(), UriKind.Absolute, out var parsed))
        {
            return null;
        }
        return parsed.Scheme == Uri.UriSchemeHttps && !String.IsNullOrEmpty(parsed.Host)
            ? parsed.AbsoluteUri
            : null;
    }
}
```

Note: .NET's `Uri.TryCreate` does not strip interior tab/LF like WHATWG, so `Trim()` covers the leading/trailing case the tests pin. If the normalized-form tests fail on exact string shape (e.g. trailing slash added for pathless URLs), adjust the *test expectations* to .NET's `AbsoluteUri` output — the invariant that matters is `https:` + non-empty host + returned-string-is-opened-string, not byte-for-byte WHATWG parity.

- [ ] **Step 5: Run tests to green**, then **mutation-verify**: copy `UrlGuard.cs` to the scratchpad, change `UriSchemeHttps` to `UriSchemeHttp`, watch `rejects_everything_else("http://…")` fail, restore the copy, re-run to green.

- [ ] **Step 6: Commit** — `git add creative-console-plugin && git commit -m "feat(console): test project + https-only URL guard"`

---

### Task 2: Thumbnails (rewrite + capped fetch)

**Files:**
- Create: `creative-console-plugin/JackyControlPlugin/src/Core/Thumbnails.cs`
- Create: `creative-console-plugin/JackyControlPlugin/tests/JackyControlPlugin.Tests/ThumbnailsTests.cs`

- [ ] **Step 1: Failing tests** — port of `thumbnail.ts` semantics. The pure rewrite plus the fetch caps via a fake `HttpMessageHandler`:

```csharp
namespace Loupedeck.JackyControlPlugin.Tests;

using System.Net;
using Loupedeck.JackyControlPlugin;

public class ThumbnailsTests
{
    [Theory]
    [InlineData("https://i.ytimg.com/vi/abc123/maxresdefault.jpg", "https://i.ytimg.com/vi/abc123/mqdefault.jpg")]
    [InlineData("https://i.ytimg.com/vi/abc123/hqdefault.jpg", "https://i.ytimg.com/vi/abc123/mqdefault.jpg")]
    [InlineData("https://i.ytimg.com/vi_webp/abc123/sddefault.webp", "https://i.ytimg.com/vi_webp/abc123/mqdefault.webp")]
    [InlineData("https://i.ytimg.com/vi/abc123/mqdefault.jpg", "https://i.ytimg.com/vi/abc123/mqdefault.jpg")]
    // Unknown host / shape / variant: untouched.
    [InlineData("https://covers.example/album.jpg", "https://covers.example/album.jpg")]
    [InlineData("https://i.ytimg.com/vi/abc123/unknownvariant.jpg", "https://i.ytimg.com/vi/abc123/unknownvariant.jpg")]
    [InlineData("not a url", "not a url")]
    public void rewrites_known_youtube_variants_only(string input, string expected)
        => Assert.Equal(expected, Thumbnails.SmallThumbnailUrl(input));

    private static HttpClient ClientReturning(HttpStatusCode status, byte[] body, string contentType = "image/jpeg")
        => new(new FakeHandler(req => {
            var res = new HttpResponseMessage(status) { Content = new ByteArrayContent(body) };
            res.Content.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue(contentType);
            return res;
        }));

    [Fact]
    public async Task fetch_returns_bytes_for_ok_image()
    {
        var bytes = new byte[] { 1, 2, 3 };
        var result = await Thumbnails.LoadAsync("https://i.ytimg.com/vi/x/mqdefault.jpg", ClientReturning(HttpStatusCode.OK, bytes));
        Assert.Equal(bytes, result);
    }

    [Fact]
    public async Task fetch_rejects_non_image_content_type()
        => Assert.Null(await Thumbnails.LoadAsync("https://x.example/a.jpg", ClientReturning(HttpStatusCode.OK, new byte[] { 1 }, "text/html")));

    [Fact]
    public async Task fetch_rejects_oversize_and_empty_bodies()
    {
        Assert.Null(await Thumbnails.LoadAsync("https://x.example/a.jpg", ClientReturning(HttpStatusCode.OK, new byte[Thumbnails.MaxBytes + 1])));
        Assert.Null(await Thumbnails.LoadAsync("https://x.example/a.jpg", ClientReturning(HttpStatusCode.OK, Array.Empty<byte>())));
    }

    [Fact]
    public async Task fetch_returns_null_on_http_error_or_exception()
    {
        Assert.Null(await Thumbnails.LoadAsync("https://x.example/a.jpg", ClientReturning(HttpStatusCode.NotFound, new byte[] { 1 })));
        var throwing = new HttpClient(new FakeHandler(_ => throw new HttpRequestException("down")));
        Assert.Null(await Thumbnails.LoadAsync("https://x.example/a.jpg", throwing));
    }

    [Fact]
    public async Task fetch_requests_the_rewritten_url_not_the_original()
    {
        Uri requested = null;
        var client = new HttpClient(new FakeHandler(req => {
            requested = req.RequestUri;
            var res = new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(new byte[] { 1 }) };
            res.Content.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("image/jpeg");
            return res;
        }));
        await Thumbnails.LoadAsync("https://i.ytimg.com/vi/x/maxresdefault.jpg", client);
        Assert.Equal("https://i.ytimg.com/vi/x/mqdefault.jpg", requested!.ToString());
    }
}

/// <summary>Shared fake handler for HttpClient-based tests.</summary>
public sealed class FakeHandler : HttpMessageHandler
{
    private readonly Func<HttpRequestMessage, HttpResponseMessage> _respond;
    public FakeHandler(Func<HttpRequestMessage, HttpResponseMessage> respond) => this._respond = respond;
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        => Task.FromResult(this._respond(request));
}
```

- [ ] **Step 2: Run → compile failure.**

- [ ] **Step 3: Implement `src/Core/Thumbnails.cs`**

```csharp
namespace Loupedeck.JackyControlPlugin;

using System;
using System.Net.Http;
using System.Text.RegularExpressions;
using System.Threading.Tasks;

/// <summary>
/// Port of thumbnail.ts. A console key is ~80px, so a known YouTube
/// thumbnail URL is rewritten down to mqdefault BEFORE the request, and
/// anything over the caps is a wrong URL rather than album art. Every
/// failure resolves to null — artwork is never worth breaking the key.
/// </summary>
public static class Thumbnails
{
    public const Int32 MaxBytes = 2 * 1024 * 1024;
    private const String SmallVariant = "mqdefault";
    private static readonly Regex YtThumbPath = new(@"^/(vi|vi_webp)/([^/]+)/([^/]+)\.(jpg|webp)$", RegexOptions.Compiled);
    private static readonly String[] KnownVariants = { "default", "mqdefault", "hqdefault", "sddefault", "maxresdefault" };

    public static String SmallThumbnailUrl(String raw)
    {
        if (!Uri.TryCreate(raw, UriKind.Absolute, out var u))
        {
            return raw;
        }
        if (!(u.Host == "ytimg.com" || u.Host.EndsWith(".ytimg.com", StringComparison.Ordinal)))
        {
            return raw;
        }
        var m = YtThumbPath.Match(u.AbsolutePath);
        if (!m.Success || Array.IndexOf(KnownVariants, m.Groups[3].Value) < 0)
        {
            return raw;
        }
        return new UriBuilder(u) { Path = $"/{m.Groups[1].Value}/{m.Groups[2].Value}/{SmallVariant}.{m.Groups[4].Value}" }.Uri.ToString();
    }

    public static async Task<Byte[]> LoadAsync(String url, HttpClient http)
    {
        try
        {
            using var res = await http.GetAsync(SmallThumbnailUrl(url)).ConfigureAwait(false);
            if (!res.IsSuccessStatusCode)
            {
                return null;
            }
            var type = res.Content.Headers.ContentType?.MediaType ?? "image/jpeg";
            if (!type.StartsWith("image/", StringComparison.Ordinal))
            {
                return null;
            }
            var declared = res.Content.Headers.ContentLength;
            if (declared.HasValue && declared.Value > MaxBytes)
            {
                return null;
            }
            var buf = await res.Content.ReadAsByteArrayAsync().ConfigureAwait(false);
            return buf.Length == 0 || buf.Length > MaxBytes ? null : buf;
        }
        catch
        {
            return null;
        }
    }
}
```

(No base64/64KB-encoded cap here: unlike the Stream Deck websocket, `BitmapBuilder.DrawImage` takes raw bytes, so the 2MB byte cap is the operative bound. Note that difference in the file's doc comment if you touch it.)

- [ ] **Step 4: Green**, then **mutation-verify**: break the rewrite (`SmallVariant = "maxresdefault"`) → rewrite tests fail; break the size cap (`> MaxBytes` → `> Int32.MaxValue`) → oversize test fails. Restore from copies, re-run to green.

- [ ] **Step 5: Commit** — `git commit -m "feat(console): thumbnail rewrite + capped fetch"`

---

### Task 3: ControlApiClient

**Files:**
- Create: `creative-console-plugin/JackyControlPlugin/src/Core/ControlApiClient.cs`
- Create: `creative-console-plugin/JackyControlPlugin/tests/JackyControlPlugin.Tests/ControlApiClientTests.cs`

- [ ] **Step 1: Failing tests.** Port `api-client.ts`. Cover per route: URL + method + bearer header; JSON decode of NowPlaying (active/inactive), channels, playlists, dashboard-url, summon, announce, voice; non-2xx throws `ControlApiException` with `.Status`; trailing-slash apiUrl normalized; voice sends `audio/wav` body with `?language=` and `&debug=1` only when set. Representative tests (write ALL of these):

```csharp
namespace Loupedeck.JackyControlPlugin.Tests;

using System.Net;
using System.Text;
using Loupedeck.JackyControlPlugin;

public class ControlApiClientTests
{
    private static (ControlApiClient client, List<HttpRequestMessage> seen, Func<string> lastBody)
        Rig(HttpStatusCode status = HttpStatusCode.OK, string json = "{}")
    {
        var seen = new List<HttpRequestMessage>();
        string lastBody = null;
        var handler = new FakeHandler(req => {
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
    public async Task voice_decodes_client_directives()
    {
        var (client, _, _) = Rig(json: """{"transcript":"t","actions":[{"action":"open_dashboard","ok":true,"detail":"d"}],"ok":true,"detail":"done","client":[{"type":"open_url","url":"https://x.test/d"}]}""");
        var result = await client.VoiceCommandAsync(new byte[] { 1 }, null, false);
        Assert.Single(result.Client);
        Assert.Equal("open_url", result.Client[0].Type);
    }

    // ...same-shape tests for SummonAsync (posts guildId+channelId, decodes action/sessionCode),
    // ChannelsAsync / PlaylistsAsync (decode arrays), PlayPlaylistAsync (posts, decodes inserted),
    // DashboardUrlAsync (decodes active+url), StopAsync/ShuffleAsync (route + method).
}
```

- [ ] **Step 2: Run → compile failure.**

- [ ] **Step 3: Implement `src/Core/ControlApiClient.cs`.** DTOs as records with `System.Text.Json` `JsonPropertyName` matching the wire (camelCase), all reads via `JsonSerializerOptions { PropertyNameCaseInsensitive = true }`:

```csharp
namespace Loupedeck.JackyControlPlugin;

using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
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

/// <summary>Port of api-client.ts: bearer-authed typed client over /control/*.</summary>
public sealed class ControlApiClient
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNameCaseInsensitive = true };
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

    private async Task<String> SendAsync(HttpRequestMessage req)
    {
        using var res = await this._http.SendAsync(req).ConfigureAwait(false);
        if (!res.IsSuccessStatusCode)
        {
            throw new ControlApiException((Int32)res.StatusCode);
        }
        return await res.Content.ReadAsStringAsync().ConfigureAwait(false);
    }

    private Task<String> PostAsync(String path, Object body = null)
    {
        var req = this.Request(HttpMethod.Post, path);
        req.Content = new StringContent(body == null ? "{}" : JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");
        return this.SendAsync(req);
    }

    private Task<String> GetAsync(String path) => this.SendAsync(this.Request(HttpMethod.Get, path));

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
        return JsonSerializer.Deserialize<VoiceResult>(await this.SendAsync(req).ConfigureAwait(false), Json);
    }
}

public sealed record PlayPlaylistResult(Int32 Inserted, String PlaylistName);
```

Timeouts live on the shared `HttpClient` (10 s) except voice's 30 s: give `VoiceCommandAsync` a per-request `CancellationTokenSource` with 30 s and pass its token to `SendAsync` (add an optional `CancellationToken` parameter threaded to `_http.SendAsync`). The plugin constructs the HttpClient in Task 6.

- [ ] **Step 4: Green.** **Mutation-verify** the two guards that matter: remove the bearer header → header test fails; make `SendAsync` swallow non-2xx → status test fails. Restore, green.

- [ ] **Step 5: Commit** — `git commit -m "feat(console): typed control API client"`

---

### Task 4: AuthFlow

**Files:**
- Create: `creative-console-plugin/JackyControlPlugin/src/Core/AuthFlow.cs`
- Create: `creative-console-plugin/JackyControlPlugin/tests/JackyControlPlugin.Tests/AuthFlowTests.cs`

- [ ] **Step 1: Failing tests.** Port `auth.ts`: POST `{base}/control/auth/start` (no auth header) → `{state, authorizeUrl}`; authorize URL must pass `UrlGuard.OpenableUrl` (reject → `SignInException(502, "unsafe-authorize-url")`, browser NOT opened); open the **guarded** string; poll `GET {base}/control/auth/poll?state=…` every interval; 202 → keep polling until deadline (timeout → `SignInException(408, "timeout")`); other non-2xx → `SignInException(status, code-from-body)`; 200 → `SignInResult(Token, DiscordUserId, DiscordUserName)`. Make delay and clock injectable:

```csharp
public sealed class AuthFlow
{
    public AuthFlow(HttpClient http, Func<Task> delay = null, Func<DateTimeOffset> now = null) { /* defaults: Task.Delay(2000), DateTimeOffset.UtcNow */ }
    public async Task<SignInResult> SignInAsync(String apiUrl, Action<String> openUrl) { ... }
}
public sealed record SignInResult(String Token, String DiscordUserId, String DiscordUserName);
public sealed class SignInException : Exception
{
    public Int32 Status { get; }
    public String Code { get; }
}
```

Tests (all with `FakeHandler` variants that script a sequence of responses; keep a mutable queue):
- success: start 200 `{"state":"s1","authorizeUrl":"https://d.test/auth"}`, poll 202 then 200 `{"token":"t","discordUserId":"1","discordUserName":"Jacob"}` → result carries all three; `openUrl` was called with `https://d.test/auth` (the guarded string).
- unsafe authorize URL (`javascript:...` or `http:`) → `SignInException` 502 `unsafe-authorize-url`, `openUrl` NEVER called.
- poll 403 with body `{"error":"not-a-member"}` → exception carries 403 + code.
- timeout: `now` advances past the 5-minute deadline while polls return 202 → 408 `timeout`.
- start failure 503 → exception 503.
- poll URL contains `?state=s1` URL-escaped.

- [ ] **Step 2: Run → fail. Step 3: Implement** faithfully to `auth.ts` (5-minute deadline, 2 s interval, `errorCodeOf` = try parse `{"error": …}` else null). **Step 4: Green + mutation-verify** the URL guard wiring: bypass the guard (open the raw `authorizeUrl`) → unsafe test fails because `openUrl` got called. Restore.

- [ ] **Step 5: Commit** — `git commit -m "feat(console): OAuth sign-in flow with guarded authorize URL"`

---

### Task 5: SessionPoller + KeyText

**Files:**
- Create: `creative-console-plugin/JackyControlPlugin/src/Core/SessionPoller.cs`
- Create: `creative-console-plugin/JackyControlPlugin/src/Core/KeyText.cs`
- Create: `creative-console-plugin/JackyControlPlugin/tests/JackyControlPlugin.Tests/SessionPollerTests.cs`
- Create: `creative-console-plugin/JackyControlPlugin/tests/JackyControlPlugin.Tests/KeyTextTests.cs`

- [ ] **Step 1: SessionPoller failing tests.** Port `poller.ts` exactly — same states, same invariants, but idiomatic C#: `PollState` as a discriminated record (`Data(NowPlaying)`, `Offline`, `Unauthorized`, `Unconfigured`), `Subscribe/Unsubscribe(Action<PollState>)`, `Kick()`. Make the timer injectable: constructor takes `Func<NowPlaying Task>` poll fn plus a **virtual-time scheduler** — simplest is `Func<Int32, CancellationToken, Task>` delay you can complete manually in tests, or restructure as an async loop driven by `TaskCompletionSource`. Pin:
  - subscribing the first subscriber starts polling; a poll success emits `Data`.
  - `ControlApiException(401)` → `Unauthorized`; other exceptions → `Offline`; a client with no token configured → the plugin passes a poll fn that throws `ControlApiException(0)` → `Unconfigured`.
  - after 3 consecutive failures the delay goes from 5000 to 30000; a success resets it.
  - unsubscribing the last subscriber stops the loop (no further poll calls).
  - `Kick()` with a pending timer polls immediately; `Kick()` during an in-flight poll does NOT start a second chain (assert poll-fn concurrency never exceeds 1).
  - a subscriber that throws does not break the loop or other subscribers.

- [ ] **Step 2: KeyText failing tests** — the truncation the Play/Pause key draws (two lines, no marquee):

```csharp
public class KeyTextTests
{
    [Fact] public void short_title_is_one_line()
        => Assert.Equal(new[] { "Song" }, KeyText.TitleLines("Song", maxCharsPerLine: 11, maxLines: 2));
    [Fact] public void wraps_on_word_boundaries()
        => Assert.Equal(new[] { "Never Gonna", "Give You Up" }, KeyText.TitleLines("Never Gonna Give You Up", 11, 2));
    [Fact] public void truncates_with_ellipsis_past_two_lines()
        => Assert.Equal(new[] { "Never Gonna", "Give You U…" }, KeyText.TitleLines("Never Gonna Give You Uppppp", 11, 2));
    [Fact] public void hard_breaks_a_single_overlong_word()
        => Assert.Equal(new[] { "Supercalifr", "agilistice…" }, KeyText.TitleLines("Supercalifragilisticexpialidocious", 11, 2));
    [Fact] public void null_or_empty_gives_no_lines()
        => Assert.Empty(KeyText.TitleLines(null, 11, 2));
}
```

- [ ] **Step 3: Implement both.** `KeyText.TitleLines`: greedy word wrap; a word longer than the line hard-breaks; if content remains after `maxLines`, the last line is cut to `maxCharsPerLine - 1` chars + `…`.

- [ ] **Step 4: Green + mutation-verify** the poller's one-chain invariant (make `Kick()` always start a tick → concurrency test fails) and backoff (never back off → backoff test fails). Restore.

- [ ] **Step 5: Commit** — `git commit -m "feat(console): session poller + key text truncation"`

---

### Task 6: Plugin wiring + simple commands

**Files:**
- Modify: `creative-console-plugin/JackyControlPlugin/src/JackyControlPlugin.cs`
- Delete: `src/Actions/CounterCommand.cs`, `src/Actions/CounterAdjustment.cs`
- Create: `src/Actions/SkipCommand.cs`, `StopCommand.cs`, `ShuffleCommand.cs`, `VolumeButtonsCommand.cs`, `VolumeAdjustment.cs`, `DashboardCommand.cs`, `AnnounceCommand.cs`

No new unit tests in this task (SDK-facing glue); the gate is `dotnet build` + existing tests staying green. Keep every action thin: parse/format on the pure classes, one client call, key feedback.

- [ ] **Step 1: Shared services on the Plugin subclass.** In `JackyControlPlugin.cs`:

```csharp
namespace Loupedeck.JackyControlPlugin
{
    using System;
    using System.Net.Http;

    public class JackyControlPlugin : Plugin
    {
        public override Boolean UsesApplicationApiOnly => true;
        public override Boolean HasNoApplication => true;

        private const String DefaultApiUrl = "https://control.jacky-music-bot.com";
        private const String ApiUrlSetting = "apiUrl";
        private const String AuthTokenSetting = "authToken";

        public HttpClient Http { get; } = new() { Timeout = TimeSpan.FromSeconds(10) };
        public SessionPoller Poller { get; private set; }

        public String ApiUrl => this.TryGetPluginSetting(ApiUrlSetting, out var url) && !String.IsNullOrWhiteSpace(url) ? url : DefaultApiUrl;
        public String AuthToken => this.TryGetPluginSetting(AuthTokenSetting, out var token) ? token : null;
        public Boolean SignedIn => !String.IsNullOrEmpty(this.AuthToken);

        /// <summary>A client for the current settings. Cheap: state lives in HttpClient/settings.</summary>
        public ControlApiClient Client() => new(this.Http, this.ApiUrl, this.AuthToken);

        public void SaveSignIn(String apiUrl, String token)
        {
            this.SetPluginSetting(ApiUrlSetting, apiUrl);
            this.SetPluginSetting(AuthTokenSetting, token);   // never logged
            this.Poller.Kick();
        }

        public JackyControlPlugin()
        {
            PluginLog.Init(this.Log);
            PluginResources.Init(this.Assembly);
        }

        public override void Load()
            => this.Poller = new SessionPoller(() =>
                this.SignedIn ? this.Client().NowPlayingAsync() : throw new ControlApiException(0));

        public override void Unload() => this.Http.Dispose();
    }
}
```

(Adapt the poller construction to Task 5's actual constructor signature. An unsigned-in poll must surface as `Unconfigured` — the `ControlApiException(0)` convention from the Stream Deck plugin.)

- [ ] **Step 2: Simple commands.** Pattern (SkipCommand shown; Stop/Shuffle identical with their routes and names):

```csharp
namespace Loupedeck.JackyControlPlugin
{
    using System;

    public class SkipCommand : PluginDynamicCommand
    {
        private JackyControlPlugin JackyPlugin => (JackyControlPlugin)this.Plugin;

        public SkipCommand() : base("Skip", "Skip to the next track", "Jacky Music") { }

        protected override void RunCommand(String actionParameter)
            => _ = this.RunAsync();

        private async System.Threading.Tasks.Task RunAsync()
        {
            try
            {
                await this.JackyPlugin.Client().SkipAsync().ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                PluginLog.Info($"skip failed: {(ex as ControlApiException)?.Status.ToString() ?? ex.GetType().Name}");
            }
        }
    }
}
```

Log status codes only — never bodies, tokens, or transcripts.

- [ ] **Step 3: VolumeButtonsCommand** — one command, two parameters:

```csharp
public class VolumeButtonsCommand : PluginDynamicCommand
{
    public VolumeButtonsCommand() : base()
    {
        this.AddParameter("up", "Volume +5", "Jacky Music");
        this.AddParameter("down", "Volume −5", "Jacky Music");
    }
    protected override void RunCommand(String actionParameter)
        => _ = this.SendAsync(actionParameter == "up" ? 5 : -5);
    // SendAsync: try client.VolumeAsync(delta), log status on failure.
}
```

- [ ] **Step 4: VolumeAdjustment** — `PluginDynamicAdjustment("Volume", "Adjust playback volume", "Jacky Music", hasReset: false)`; `ApplyAdjustment(actionParameter, diff)` → `VolumeAsync(diff * 5)` fire-and-forget, then `AdjustmentValueChanged()`; `GetAdjustmentValue` returns the last volume seen by the poller (subscribe in the adjustment's `OnLoad`/constructor to cache `NowPlaying.Volume`; show `"—"` when not `Data`).

- [ ] **Step 5: DashboardCommand** — port of the Stream Deck dashboard key: `DashboardUrlAsync()`, then `var safe = UrlGuard.OpenableUrl(result.Url); if (safe != null) System.Diagnostics.Process.Start(new ProcessStartInfo(safe) { UseShellExecute = true });` — **open `safe`, never `result.Url`**, and do nothing (log status only) when the guard rejects.

- [ ] **Step 6: AnnounceCommand** — parameterised fan-out, porting the Stream Deck announce key semantics:

```csharp
public class AnnounceCommand : PluginDynamicCommand
{
    public AnnounceCommand() : base()
    {
        this.AddParameter("session", "Post session code", "Jacky Music###Post to Discord");
        this.AddParameter("nowplaying", "Post now playing", "Jacky Music###Post to Discord");
        this.AddParameter("queue", "Post queue", "Jacky Music###Post to Discord");
        this.AddParameter("status", "Post status", "Jacky Music###Post to Discord");
    }
    // RunCommand: AnnounceAsync(actionParameter).
    //   ControlApiException 429 → transient key label "Just posted"
    //   result.Ok == false     → transient key label result.Detail (e.g. "Queue is empty")
    //   success                → brief ✓
    // Transient label: store per-parameter (string label, DateTime until) and call
    // ActionImageChanged(actionParameter); GetCommandImage draws the label while
    // DateTime.UtcNow < until (3 s), else the parameter's display name/glyph.
}
```

- [ ] **Step 7: Build + full test run green; commit** — `git commit -m "feat(console): plugin wiring + playback, volume, dashboard, announce actions"`

---

### Task 7: PlayPauseCommand with live image

**Files:**
- Create: `src/Actions/PlayPauseCommand.cs`

- [ ] **Step 1: Implement.** Subscribes to the poller (first `GetCommandImage` call or `OnLoad`), caches the latest `PollState` and fetched artwork bytes, and:
  - `RunCommand`: `PlayPauseAsync()`; on failure log status.
  - On each poll `Data`: if the track's `Thumbnail` changed, `Thumbnails.LoadAsync` it (fire-and-forget) and cache the bytes; then `ActionImageChanged()`.
  - `GetCommandImage(actionParameter, imageSize)`:

```csharp
protected override BitmapImage GetCommandImage(String actionParameter, PluginImageSize imageSize)
{
    using var bmp = new BitmapBuilder(imageSize);
    bmp.Clear(new BitmapColor(26, 26, 46));                       // #1a1a2e, the family idiom
    var accent = new BitmapColor(233, 69, 96);                    // #e94560
    var state = this._lastState;
    if (state is not SessionPoller.Data data || !data.NowPlaying.Active)
    {
        bmp.DrawText(state is SessionPoller.Unauthorized ? "Sign in" : "♪", accent, fontSize: 14);
        return bmp.ToImage();
    }
    if (this._artwork != null && BitmapImage.TryCreateFromArray(this._artwork, out var art))
    {
        bmp.DrawImage(art, 0, 0);                                  // artwork under the text band
        bmp.FillRectangle(0, bmp.Height - 26, bmp.Width, 26, new BitmapColor(26, 26, 46, 200));
    }
    var lines = KeyText.TitleLines(data.NowPlaying.Title, maxCharsPerLine: 11, maxLines: 2);
    for (var i = 0; i < lines.Length; i++)
    {
        bmp.DrawText(lines[i], 0, bmp.Height - 26 + (i * 12), bmp.Width, 12, BitmapColor.White, fontSize: 9);
    }
    if (data.NowPlaying.Paused)
    {
        bmp.DrawText("⏸", 2, 2, 16, 16, accent, fontSize: 12);
    }
    return bmp.ToImage();
}
```

  Adjust overload arguments to compile against the reflected `DrawText` signatures (`(text, x, y, w, h, color?, fontSize, ...)` — remaining Int32s default; check `sdk/PluginApi.xml` if a call doesn't resolve). Scale artwork: `DrawImage` at native size is fine for mqdefault since `BitmapBuilder` clips; if the SDK offers no scaling overload, draw and accept the crop — the doc'd alternative is `SetBackgroundImage(art)`, use it if it letterboxes better. This is on-device-verifiable only; keep the drawing code isolated so tweaks after the user's hardware pass are one-file.

- [ ] **Step 2: Build green, tests green; commit** — `git commit -m "feat(console): play/pause key with live artwork and title"`

---

### Task 8: Action Editor actions — SignIn, Summon, PlayPlaylist

**Files:**
- Create: `src/Actions/SignInCommand.cs`, `src/Actions/SummonCommand.cs`, `src/Actions/PlayPlaylistCommand.cs`

- [ ] **Step 1: SignInCommand** (`ActionEditorCommand`):
  - Editor: `ActionEditorTextbox("serverUrl", "Server URL")` with the default URL as placeholder/description.
  - `RunCommand(parameters)`: read `serverUrl` (fall back to plugin default), then `AuthFlow.SignInAsync(url, open)` where `open` = guarded-string `Process.Start` as in DashboardCommand (AuthFlow already guards; still open exactly the string it passes). On success `plugin.SaveSignIn(url, result.Token)` and `ActionImageChanged()`. On `SignInException` log `status:code` only. **Never log the token.** Fire-and-forget the async work from `RunCommand` (return `true` immediately).
  - `GetCommandImage`: "Signed in as ✓" style vs "Sign in" (green/red accent) off `plugin.SignedIn`.
- [ ] **Step 2: SummonCommand** (`ActionEditorCommand`):
  - Editor: `ActionEditorListbox("guild", "Server")` + `ActionEditorListbox("channel", "Voice channel")`.
  - `ListboxItemsRequested`: if `e.ControlName.EqualsNoCase("guild")` → `ChannelsAsync()`, `e.AddItem(g.GuildId, g.GuildName, null)` per guild. For `"channel"` → items of the currently selected guild (`e.ActionEditorState.GetControlValue("guild")`). On `ControlValueChanged` for `guild` → `this.ActionEditor.ListboxItemsChanged("channel")`. API errors: add no items, log status.
  - `RunCommand`: `TryGetString` both; missing either → return false. `SummonAsync(guild, channel)`; result `action == "left"` vs `"joined"` decides a transient label ("Left" / "Joined ▸ code" — sessionCode may be null).
- [ ] **Step 3: PlayPlaylistCommand**: same shape with `PlaylistsAsync()`; run posts `PlayPlaylistAsync(guild, playlistName)` and shows `"+N"` inserted transiently.
- [ ] **Step 4: Build + tests green; commit** — `git commit -m "feat(console): sign-in, summon, playlist editor actions"`

---

### Task 9: Voice — MicRecorder + VoiceCommand

**Files:**
- Create: `src/Audio/MicRecorder.cs`
- Create: `src/Actions/VoiceCommand.cs`
- Modify: `src/JackyControlPlugin.csproj` (add `<PackageReference Include="NAudio" Version="2.2.1" />`)

- [ ] **Step 1: MicRecorder** (Windows-only, NAudio `WaveInEvent`):

```csharp
namespace Loupedeck.JackyControlPlugin;

using System;
using System.Collections.Generic;
using System.IO;
using NAudio.Wave;

/// <summary>16 kHz mono 16-bit WAV captured entirely in memory. The recording
/// never touches disk and is discarded after the POST.</summary>
public sealed class MicRecorder : IDisposable
{
    public const Int32 MaxSeconds = 15;
    private WaveInEvent _waveIn;
    private MemoryStream _buffer;
    private WaveFileWriter _writer;

    public static IReadOnlyList<(Int32 Number, String Name)> Devices()
    {
        var list = new List<(Int32, String)>();
        for (var i = 0; i < WaveInEvent.DeviceCount; i++)
        {
            list.Add((i, WaveInEvent.GetCapabilities(i).ProductName));
        }
        return list;
    }

    public Boolean Recording => this._waveIn != null;
    public event EventHandler MaxDurationReached;

    public void Start(Int32 deviceNumber)
    {
        if (this.Recording) return;
        this._buffer = new MemoryStream();
        this._waveIn = new WaveInEvent { DeviceNumber = deviceNumber, WaveFormat = new WaveFormat(16000, 16, 1) };
        this._writer = new WaveFileWriter(new IgnoreDisposeStream(this._buffer), this._waveIn.WaveFormat);
        this._waveIn.DataAvailable += (_, e) =>
        {
            this._writer.Write(e.Buffer, 0, e.BytesRecorded);
            if (this._writer.TotalTime.TotalSeconds >= MaxSeconds)
            {
                this.MaxDurationReached?.Invoke(this, EventArgs.Empty);
            }
        };
        this._waveIn.StartRecording();
    }

    /// <summary>Stops and returns the complete WAV, or null if nothing was captured.</summary>
    public Byte[] Stop()
    {
        if (!this.Recording) return null;
        this._waveIn.StopRecording();
        this._writer.Dispose();          // finalizes the WAV header into _buffer
        this._waveIn.Dispose();
        this._waveIn = null;
        var wav = this._buffer.ToArray();
        this._buffer.Dispose();
        // A header-only file means zero audio frames — treat as no capture,
        // mirroring the Stream Deck plugin's zero-byte lesson.
        return wav.Length > 44 ? wav : null;
    }

    public void Dispose() => this.Stop();
}
```

(`IgnoreDisposeStream` is NAudio's `NAudio.Utils.IgnoreDisposeStream`; it keeps `_buffer` readable after the writer is disposed.)

- [ ] **Step 2: VoiceCommand** (`ActionEditorCommand`, press-to-toggle):
  - Editor: `ActionEditorListbox("microphone", "Microphone")` filled from `MicRecorder.Devices()` (name = device number as string; **an unconfigured key falls back to device 0** — the "no default device" lesson); `ActionEditorListbox("language", "Language")` with the server's codes (`en, ko, ja, es, fr, de, zh` — display names "English", "한국어", "日本語", "Español", "Français", "Deutsch", "中文") plus an "Auto" empty-value first item; `ActionEditorCheckbox("debug", "Print debug message to Discord")`.
  - State machine in the action (one recorder instance): idle —press→ `Start(device)`, red ● image via `ActionImageChanged()`; recording —press→ `Stop()` → null ⇒ transient "No audio"; else POST `VoiceCommandAsync(wav, language, debug)`; `MaxDurationReached` (marshal via a flag; stop on the next event or a `System.Threading.Timer` at 15 s as backstop) behaves like a press-stop.
  - Result rendering (port of the Stream Deck voice key): HTTP 422 → "Didn't catch that"; 400 → "No audio"; 429 → "Just posted"; 401 → "Sign in"; ok:false with `Detail` → the detail; ok:true → ✓ (+`Detail` if present). Then walk `result.Client`: for each directive with `Type == "open_url"`, `var safe = UrlGuard.OpenableUrl(d.Url)` and open **safe** only.
  - **Transcripts and audio must never be logged.** Log only status codes and byte counts.
- [ ] **Step 3: Build + tests green; commit** — `git commit -m "feat(console): press-to-toggle voice command with NAudio capture"`

---

### Task 10: Packaging, docs, delivery

**Files:**
- Modify: `src/package/metadata/LoupedeckPackage.yaml`
- Create: `docs/creative-console-control.md`
- Modify: `docs/streamdeck-control.md` (one cross-link line), `CLAUDE.md` (repo-structure line for `creative-console-plugin/`)

- [ ] **Step 1: Metadata.** In `LoupedeckPackage.yaml` set displayName "Jacky Control", author "Jacob Choi", a one-line description, and add the MX Creative Console to `supportedDevices` (the generated file lists the valid device-family names in comments; include the Creative Console family alongside the generated default). Keep the generated Icon256x256.png unless a branded one is trivial to produce with the existing icon idiom (dark `#1a1a2e` rounded square, `#e94560` glyph).
- [ ] **Step 2: Package.** `logiplugintool package` (run `logiplugintool --help` / `logiplugintool package --help` for the exact invocation; it consumes the built output + yaml and emits `JackyControl.lplug4`). Copy the artifact to `creative-console-plugin/JackyControl.lplug4` — decide gitignore vs commit by matching what `streamdeck-plugin/` does with its packaged artifact (it keeps them untracked; do the same).
- [ ] **Step 3: Docs.** `docs/creative-console-control.md` mirroring `docs/streamdeck-control.md`: install (.lplug4 double-click with Logi Options+ installed), sign-in flow + same-address binding note, every action's behaviour and error vocabulary, voice press-to-toggle + Windows-only note, build-from-source (fetch-pluginapi.ps1), and an **on-device checklist** for the user's manual pass (key images render; dial adjusts volume; editor listboxes populate; voice toggle records and posts; announce 429 shows "Just posted").
- [ ] **Step 4: Full `dotnet test` + `make lint` (repo Python untouched — just confirm no collateral), commit** — `git commit -m "feat(console): package metadata, .lplug4, and docs"`.
- [ ] **Step 5:** Merge `feat/creative-console` to master per the repo's finishing flow, and deliver `JackyControl.lplug4` to the user via SendUserFile with the on-device checklist.

---

## Self-review notes

- Spec coverage: sign-in (T8), play/pause+image (T7), skip/stop/shuffle (T6), volume dial+buttons (T6), announce fan-out incl. 429 (T6), playlist+summon editors (T8), dashboard guard (T6), voice toggle+NAudio+language+debug (T9), packaging+docs (T10), tests for client/auth/poller/guard/thumbnails/truncation (T1–T5). No bot changes anywhere — confirmed.
- The one intentional divergence from `thumbnail.ts` (no 64KB encoded cap — raw bytes, no websocket) is documented in Task 2.
- SDK-call shapes in Tasks 6–9 are grounded in the reflected surface listed under Environment facts; where an overload is ambiguous the task says to consult `sdk/PluginApi.xml` rather than guess.
