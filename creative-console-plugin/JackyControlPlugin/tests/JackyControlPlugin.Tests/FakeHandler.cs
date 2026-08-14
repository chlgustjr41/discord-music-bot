namespace Loupedeck.JackyControlPlugin.Tests;

/// <summary>Shared fake handler for HttpClient-based tests.</summary>
public sealed class FakeHandler : HttpMessageHandler
{
    private readonly Func<HttpRequestMessage, HttpResponseMessage> _respond;
    public FakeHandler(Func<HttpRequestMessage, HttpResponseMessage> respond) => this._respond = respond;
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        => Task.FromResult(this._respond(request));
}
