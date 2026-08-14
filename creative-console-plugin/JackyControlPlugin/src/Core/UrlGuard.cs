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
