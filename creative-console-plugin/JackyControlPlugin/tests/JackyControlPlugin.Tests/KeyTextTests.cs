namespace Loupedeck.JackyControlPlugin.Tests;

using Loupedeck.JackyControlPlugin;

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
