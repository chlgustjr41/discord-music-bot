namespace Loupedeck.JackyControlPlugin.Tests;

using System.Net;
using System.Text;

using Loupedeck.JackyControlPlugin;

public class AuthFlowTests
{
    private static HttpResponseMessage Json(HttpStatusCode status, string json)
        => new(status) { Content = new StringContent(json, Encoding.UTF8, "application/json") };

    /// <summary>Scripted-response rig: each request consumes the next queued response.</summary>
    private static (HttpClient http, List<HttpRequestMessage> seen) Rig(params HttpResponseMessage[] responses)
    {
        var queue = new Queue<HttpResponseMessage>(responses);
        var seen = new List<HttpRequestMessage>();
        var handler = new FakeHandler(req =>
        {
            seen.Add(req);
            return queue.Dequeue();
        });
        return (new HttpClient(handler), seen);
    }

    [Fact]
    public async Task success_path_opens_guarded_url_and_returns_identity()
    {
        var (http, seen) = Rig(
            Json(HttpStatusCode.OK, """{"state":"s1","authorizeUrl":"https://d.test/auth"}"""),
            new HttpResponseMessage(HttpStatusCode.Accepted),
            Json(HttpStatusCode.OK, """{"token":"t","discordUserId":"1","discordUserName":"Jacob"}"""));
        var opened = new List<string>();
        var flow = new AuthFlow(http, delay: () => Task.CompletedTask);

        var result = await flow.SignInAsync("https://api.test/", opened.Add);

        Assert.Equal("t", result.Token);
        Assert.Equal("1", result.DiscordUserId);
        Assert.Equal("Jacob", result.DiscordUserName);
        Assert.Equal(new[] { "https://d.test/auth" }, opened);

        // Start: POST {base}/control/auth/start with NO auth header, trailing slash trimmed.
        Assert.Equal("https://api.test/control/auth/start", seen[0].RequestUri!.ToString());
        Assert.Equal(HttpMethod.Post, seen[0].Method);
        Assert.Null(seen[0].Headers.Authorization);
        // Polls: GET {base}/control/auth/poll?state=...
        Assert.Equal(HttpMethod.Get, seen[1].Method);
        Assert.Equal("https://api.test/control/auth/poll?state=s1", seen[1].RequestUri!.ToString());
        Assert.Equal(3, seen.Count);
    }

    [Theory]
    [InlineData("javascript:alert(1)")]
    [InlineData("http://d.test/auth")]
    public async Task unsafe_authorize_url_fails_502_and_never_opens_browser(string authorizeUrl)
    {
        var (http, _) = Rig(
            Json(HttpStatusCode.OK, $$"""{"state":"s1","authorizeUrl":"{{authorizeUrl}}"}"""));
        var opened = new List<string>();
        var flow = new AuthFlow(http, delay: () => Task.CompletedTask);

        var ex = await Assert.ThrowsAsync<SignInException>(() => flow.SignInAsync("https://api.test", opened.Add));

        Assert.Equal(502, ex.Status);
        Assert.Equal("unsafe-authorize-url", ex.Code);
        Assert.Empty(opened);
    }

    [Fact]
    public async Task poll_403_carries_status_and_error_code()
    {
        var (http, _) = Rig(
            Json(HttpStatusCode.OK, """{"state":"s1","authorizeUrl":"https://d.test/auth"}"""),
            Json(HttpStatusCode.Forbidden, """{"error":"not-a-member"}"""));
        var flow = new AuthFlow(http, delay: () => Task.CompletedTask);

        var ex = await Assert.ThrowsAsync<SignInException>(() => flow.SignInAsync("https://api.test", _ => { }));

        Assert.Equal(403, ex.Status);
        Assert.Equal("not-a-member", ex.Code);
    }

    [Fact]
    public async Task polling_past_the_deadline_times_out_408()
    {
        var (http, _) = Rig(
            Json(HttpStatusCode.OK, """{"state":"s1","authorizeUrl":"https://d.test/auth"}"""),
            new HttpResponseMessage(HttpStatusCode.Accepted),
            new HttpResponseMessage(HttpStatusCode.Accepted));
        var current = DateTimeOffset.UnixEpoch;
        var flow = new AuthFlow(
            http,
            delay: () => { current += TimeSpan.FromMinutes(3); return Task.CompletedTask; },
            now: () => current);

        var ex = await Assert.ThrowsAsync<SignInException>(() => flow.SignInAsync("https://api.test", _ => { }));

        Assert.Equal(408, ex.Status);
        Assert.Equal("timeout", ex.Code);
    }

    [Fact]
    public async Task start_failure_carries_status()
    {
        var (http, _) = Rig(new HttpResponseMessage(HttpStatusCode.ServiceUnavailable)
        {
            Content = new StringContent("<html>down</html>", Encoding.UTF8, "text/html"),
        });
        var opened = new List<string>();
        var flow = new AuthFlow(http, delay: () => Task.CompletedTask);

        var ex = await Assert.ThrowsAsync<SignInException>(() => flow.SignInAsync("https://api.test", opened.Add));

        Assert.Equal(503, ex.Status);
        Assert.Null(ex.Code);
        Assert.Empty(opened);
    }

    [Fact]
    public async Task poll_url_escapes_the_state()
    {
        var (http, seen) = Rig(
            Json(HttpStatusCode.OK, """{"state":"s 1/+","authorizeUrl":"https://d.test/auth"}"""),
            Json(HttpStatusCode.OK, """{"token":"t","discordUserId":"1","discordUserName":"J"}"""));
        var flow = new AuthFlow(http, delay: () => Task.CompletedTask);

        await flow.SignInAsync("https://api.test", _ => { });

        // AbsoluteUri, not ToString(): Uri.ToString() renders %20 back as a
        // space for display, hiding whether the wire string was escaped.
        Assert.EndsWith("/control/auth/poll?state=s%201%2F%2B", seen[1].RequestUri!.AbsoluteUri);
    }
}
