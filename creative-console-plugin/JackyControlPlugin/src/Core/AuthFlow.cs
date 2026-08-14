namespace Loupedeck.JackyControlPlugin;

using System;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

public sealed record SignInResult(String Token, String DiscordUserId, String DiscordUserName);

/// <summary>
/// Why a sign-in ended without a token. The server's own error code is
/// carried through so the UI can explain the cause rather than showing a
/// bare status number. Port of auth.ts's SignInError.
/// </summary>
public sealed class SignInException : Exception
{
    public Int32 Status { get; }
    public String Code { get; }

    public SignInException(Int32 status, String code = null)
        : base($"sign-in failed ({status}{(code != null ? $": {code}" : "")})")
    {
        this.Status = status;
        this.Code = code;
    }
}

/// <summary>
/// Port of auth.ts: POST /control/auth/start (pre-auth, no bearer), open the
/// authorize URL in the browser, then poll /control/auth/poll until the user
/// completes sign-in. Throws SignInException (410 expired, 408 timed out,
/// 403 not-a-member / device-mismatch, 502 unsafe-authorize-url). The token
/// in the result is never logged here — this class does no logging at all.
/// </summary>
public sealed class AuthFlow
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNameCaseInsensitive = true };
    private static readonly TimeSpan RequestTimeout = TimeSpan.FromSeconds(10);
    private static readonly TimeSpan SignInTimeout = TimeSpan.FromMinutes(5);

    private readonly HttpClient _http;
    private readonly Func<Task> _delay;
    private readonly Func<DateTimeOffset> _now;

    private sealed record StartResponse(String State, String AuthorizeUrl);
    private sealed record TokenResponse(String Token, String DiscordUserId, String DiscordUserName);

    public AuthFlow(HttpClient http, Func<Task> delay = null, Func<DateTimeOffset> now = null)
    {
        this._http = http;
        this._delay = delay ?? (() => Task.Delay(2000));
        this._now = now ?? (() => DateTimeOffset.UtcNow);
    }

    public async Task<SignInResult> SignInAsync(String apiUrl, Action<String> openUrl)
    {
        var baseUrl = (apiUrl ?? "").TrimEnd('/');

        using var startRes = await this.SendAsync(new HttpRequestMessage(HttpMethod.Post, baseUrl + "/control/auth/start")).ConfigureAwait(false);
        if (!startRes.IsSuccessStatusCode)
        {
            throw new SignInException((Int32)startRes.StatusCode, await ErrorCodeOfAsync(startRes).ConfigureAwait(false));
        }
        var start = JsonSerializer.Deserialize<StartResponse>(await startRes.Content.ReadAsStringAsync().ConfigureAwait(false), Json);

        // Same guard as every other server-supplied URL the plugin opens, and
        // this one is the least trustworthy: /control/auth/start is pre-auth
        // by definition, so a hostile apiUrl reaches it with no token at all.
        // Fail the sign-in rather than skipping the open — polling on with no
        // browser open would leave the user staring at a spinner for five
        // minutes. The string handed to openUrl is exactly the guard's return.
        var safeAuthorizeUrl = UrlGuard.OpenableUrl(start.AuthorizeUrl);
        if (safeAuthorizeUrl == null)
        {
            throw new SignInException(502, "unsafe-authorize-url");
        }
        openUrl(safeAuthorizeUrl);

        var deadline = this._now() + SignInTimeout;
        var pollUrl = baseUrl + "/control/auth/poll?state=" + Uri.EscapeDataString(start.State);
        for (; ; )
        {
            await this._delay().ConfigureAwait(false);
            using var res = await this.SendAsync(new HttpRequestMessage(HttpMethod.Get, pollUrl)).ConfigureAwait(false);
            if (res.StatusCode == HttpStatusCode.Accepted)
            {
                if (this._now() > deadline)
                {
                    throw new SignInException(408, "timeout");
                }
                continue;
            }
            if (!res.IsSuccessStatusCode)
            {
                throw new SignInException((Int32)res.StatusCode, await ErrorCodeOfAsync(res).ConfigureAwait(false));
            }
            var body = JsonSerializer.Deserialize<TokenResponse>(await res.Content.ReadAsStringAsync().ConfigureAwait(false), Json);
            return new SignInResult(body.Token, body.DiscordUserId, body.DiscordUserName);
        }
    }

    private async Task<HttpResponseMessage> SendAsync(HttpRequestMessage req)
    {
        using (req)
        {
            using var cts = new CancellationTokenSource(RequestTimeout);
            // Disposing the CTS before the caller reads the content is safe:
            // SendAsync defaults to ResponseContentRead, so the body is fully
            // buffered by the time this returns.
            return await this._http.SendAsync(req, cts.Token).ConfigureAwait(false);
        }
    }

    /// <summary>Best-effort parse of an {"error": "..."} body; null when the
    /// body is an HTML page or empty — status alone will have to do.</summary>
    private static async Task<String> ErrorCodeOfAsync(HttpResponseMessage res)
    {
        try
        {
            using var doc = JsonDocument.Parse(await res.Content.ReadAsStringAsync().ConfigureAwait(false));
            return doc.RootElement.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String
                ? error.GetString()
                : null;
        }
        catch
        {
            return null;
        }
    }
}
