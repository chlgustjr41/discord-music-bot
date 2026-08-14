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

    [Fact]
    public void null_input_is_rejected()
        => Assert.Null(UrlGuard.OpenableUrl(null!));
}
