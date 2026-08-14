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
    // A query string survives the rewrite.
    [InlineData("https://i.ytimg.com/vi/x/maxresdefault.jpg?v=1", "https://i.ytimg.com/vi/x/mqdefault.jpg?v=1")]
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
    public async Task fetch_rejects_oversize_body_when_content_length_is_missing()
    {
        // ByteArrayContent auto-declares Content-Length, which lets the
        // declared-size check mask the post-read cap; nulling the header
        // pins buf.Length > MaxBytes on its own.
        var client = new HttpClient(new FakeHandler(req => {
            var res = new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(new byte[Thumbnails.MaxBytes + 1]) };
            res.Content.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("image/jpeg");
            res.Content.Headers.ContentLength = null;
            return res;
        }));
        Assert.Null(await Thumbnails.LoadAsync("https://x.example/a.jpg", client));
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
        Uri? requested = null;
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
