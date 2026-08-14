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
/// Deliberate divergence from thumbnail.ts: no 64KB encoded cap, because
/// BitmapBuilder takes raw bytes (no websocket/base64 layer); the 2MB
/// byte cap is the operative bound.
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
            // Rewritten BEFORE the request, not after: the point is never to
            // pull 1280x720 down a link in the first place.
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
            // Check the declared size BEFORE buffering; servers that omit
            // content-length still fall through to the post-read check.
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
