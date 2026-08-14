namespace Loupedeck.JackyControlPlugin.Tests;

using Loupedeck.JackyControlPlugin;

public class TransientLabelsTests
{
    [Fact]
    public void set_label_is_current_until_the_deadline()
    {
        var now = 1000L;
        var labels = new TransientLabels(showMs: 3000, now: () => now);
        labels.Set("k", "Joined AB12", () => { });
        Assert.Equal("Joined AB12", labels.Current("k"));
        now += 2999;
        Assert.Equal("Joined AB12", labels.Current("k"));
        now += 1; // exactly the deadline: expired
        Assert.Null(labels.Current("k"));
    }

    [Fact]
    public void unknown_key_has_no_label()
        => Assert.Null(new TransientLabels().Current("never-set"));

    [Fact]
    public void null_key_is_a_valid_key()
    {
        var labels = new TransientLabels(now: () => 0);
        labels.Set(null, "✓", () => { });
        Assert.Equal("✓", labels.Current(null));
    }

    [Fact]
    public void keys_are_independent()
    {
        var labels = new TransientLabels(now: () => 0);
        labels.Set("a", "Left", () => { });
        labels.Set("b", "+3", () => { });
        Assert.Equal("Left", labels.Current("a"));
        Assert.Equal("+3", labels.Current("b"));
    }

    [Fact]
    public async Task repaint_runs_immediately_and_again_after_expiry()
    {
        var repaints = 0;
        var labels = new TransientLabels(showMs: 10);
        labels.Set("k", "✓", () => Interlocked.Increment(ref repaints));
        Assert.True(repaints >= 1); // the label-on repaint is synchronous
        // The revert repaint is scheduled; poll rather than trusting a single sleep.
        var deadline = Environment.TickCount64 + 5000;
        while (Volatile.Read(ref repaints) < 2 && Environment.TickCount64 < deadline)
        {
            await Task.Delay(10);
        }
        Assert.Equal(2, repaints);
    }
}
