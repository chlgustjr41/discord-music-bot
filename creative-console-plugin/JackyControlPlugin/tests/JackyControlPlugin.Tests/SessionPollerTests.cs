namespace Loupedeck.JackyControlPlugin.Tests;

using System.Collections.Concurrent;

using Loupedeck.JackyControlPlugin;

public class SessionPollerTests
{
    private static readonly TimeSpan WaitBudget = TimeSpan.FromSeconds(5);
    private static readonly NowPlaying Track = new(true, "Song", "Artist", false, 60, "Guild", "https://t.test/x.jpg");

    /// <summary>
    /// Virtual-time rig: the poller's poll fn and delay fn park on
    /// TaskCompletionSources the test completes manually, so no test ever
    /// waits on real timers. Emissions happen before the next delay is
    /// requested, so awaiting NextDelayAsync is the happens-after barrier
    /// for asserting on what subscribers saw.
    /// </summary>
    private sealed class Rig
    {
        private readonly object _lock = new();
        private readonly List<TaskCompletionSource<NowPlaying>> _polls = new();
        private readonly List<(int Ms, TaskCompletionSource<bool> Tcs)> _delays = new();
        private readonly SemaphoreSlim _pollStarted = new(0);
        private readonly SemaphoreSlim _delayStarted = new(0);
        private int _pollsTaken;
        private int _delaysTaken;
        private int _activePolls;

        public int MaxConcurrentPolls { get; private set; }

        public int PollCalls
        {
            get { lock (this._lock) { return this._polls.Count; } }
        }

        public SessionPoller NewPoller(int baseMs = 5000, int maxMs = 30000)
            => new(this.Poll, this.Delay, baseMs, maxMs);

        private Task<NowPlaying> Poll()
        {
            TaskCompletionSource<NowPlaying> tcs;
            lock (this._lock)
            {
                this._activePolls++;
                if (this._activePolls > this.MaxConcurrentPolls)
                {
                    this.MaxConcurrentPolls = this._activePolls;
                }
                tcs = new(TaskCreationOptions.RunContinuationsAsynchronously);
                this._polls.Add(tcs);
            }
            this._pollStarted.Release();
            return tcs.Task;
        }

        private Task Delay(int ms)
        {
            var tcs = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            lock (this._lock) { this._delays.Add((ms, tcs)); }
            this._delayStarted.Release();
            return tcs.Task;
        }

        public async Task<TaskCompletionSource<NowPlaying>> NextPollAsync()
        {
            Assert.True(await this._pollStarted.WaitAsync(WaitBudget), "timed out waiting for a poll to start");
            lock (this._lock) { return this._polls[this._pollsTaken++]; }
        }

        public async Task<(int Ms, TaskCompletionSource<bool> Tcs)> NextDelayAsync()
        {
            Assert.True(await this._delayStarted.WaitAsync(WaitBudget), "timed out waiting for a delay to be requested");
            lock (this._lock) { return this._delays[this._delaysTaken++]; }
        }

        /// <summary>Negative probe: did any new poll begin within ms? (Consumes the permit if one did — fine, callers assert false.)</summary>
        public Task<bool> PollStartsWithin(int ms) => this._pollStarted.WaitAsync(ms);

        public void Succeed(TaskCompletionSource<NowPlaying> poll, NowPlaying data)
        {
            lock (this._lock) { this._activePolls--; }
            poll.SetResult(data);
        }

        public void Fail(TaskCompletionSource<NowPlaying> poll, Exception ex)
        {
            lock (this._lock) { this._activePolls--; }
            poll.SetException(ex);
        }
    }

    private static (Action<PollState> Cb, ConcurrentQueue<PollState> Seen) Listener()
    {
        var seen = new ConcurrentQueue<PollState>();
        return (s => seen.Enqueue(s), seen);
    }

    [Fact]
    public async Task first_subscriber_starts_polling_and_success_emits_data_to_all_subscribers()
    {
        var rig = new Rig();
        var poller = rig.NewPoller();
        var (a, seenA) = Listener();
        var (b, seenB) = Listener();

        poller.Subscribe(a);
        poller.Subscribe(b);

        var poll = await rig.NextPollAsync();
        rig.Succeed(poll, Track);
        var delay = await rig.NextDelayAsync();

        Assert.Equal(new PollState[] { new PollState.Data(Track) }, seenA.ToArray());
        Assert.Equal(new PollState[] { new PollState.Data(Track) }, seenB.ToArray());
        Assert.Equal(1, rig.PollCalls); // the second Subscribe must not start a second chain
        Assert.Equal(5000, delay.Ms);
    }

    [Fact]
    public async Task maps_errors_to_unauthorized_unconfigured_offline()
    {
        var rig = new Rig();
        var poller = rig.NewPoller();
        var (cb, seen) = Listener();
        poller.Subscribe(cb);

        rig.Fail(await rig.NextPollAsync(), new ControlApiException(401));
        (await rig.NextDelayAsync()).Tcs.SetResult(true);
        rig.Fail(await rig.NextPollAsync(), new ControlApiException(0));
        (await rig.NextDelayAsync()).Tcs.SetResult(true);
        rig.Fail(await rig.NextPollAsync(), new InvalidOperationException("boom"));
        (await rig.NextDelayAsync()).Tcs.SetResult(true);
        rig.Fail(await rig.NextPollAsync(), new ControlApiException(500));
        await rig.NextDelayAsync();

        Assert.Equal(
            new PollState[] { new PollState.Unauthorized(), new PollState.Unconfigured(), new PollState.Offline(), new PollState.Offline() },
            seen.ToArray());
    }

    [Fact]
    public async Task backs_off_after_three_consecutive_failures_and_resets_on_success()
    {
        var rig = new Rig();
        var poller = rig.NewPoller(baseMs: 5000, maxMs: 30000);
        var (cb, _) = Listener();
        poller.Subscribe(cb);

        rig.Fail(await rig.NextPollAsync(), new InvalidOperationException());
        var d1 = await rig.NextDelayAsync();
        Assert.Equal(5000, d1.Ms);
        d1.Tcs.SetResult(true);

        rig.Fail(await rig.NextPollAsync(), new InvalidOperationException());
        var d2 = await rig.NextDelayAsync();
        Assert.Equal(5000, d2.Ms);
        d2.Tcs.SetResult(true);

        rig.Fail(await rig.NextPollAsync(), new InvalidOperationException());
        var d3 = await rig.NextDelayAsync();
        Assert.Equal(30000, d3.Ms); // third consecutive failure hits maxMs
        d3.Tcs.SetResult(true);

        rig.Fail(await rig.NextPollAsync(), new InvalidOperationException());
        var d4 = await rig.NextDelayAsync();
        Assert.Equal(30000, d4.Ms); // stays backed off while failing
        d4.Tcs.SetResult(true);

        rig.Succeed(await rig.NextPollAsync(), Track);
        var d5 = await rig.NextDelayAsync();
        Assert.Equal(5000, d5.Ms); // success resets the backoff
    }

    [Fact]
    public async Task failure_count_persists_across_unsubscribe_and_resubscribe()
    {
        var rig = new Rig();
        var poller = rig.NewPoller();
        var (cb, _) = Listener();
        poller.Subscribe(cb);

        for (var i = 0; i < 3; i++)
        {
            rig.Fail(await rig.NextPollAsync(), new InvalidOperationException());
            if (i < 2)
            {
                (await rig.NextDelayAsync()).Tcs.SetResult(true);
            }
        }
        await rig.NextDelayAsync(); // 30000, left pending

        poller.Unsubscribe(cb); // stops the loop; backoff state deliberately survives
        poller.Subscribe(cb);

        rig.Fail(await rig.NextPollAsync(), new InvalidOperationException());
        var d = await rig.NextDelayAsync();
        Assert.Equal(30000, d.Ms); // still backed off: the server was just failing
    }

    [Fact]
    public async Task last_unsubscribe_stops_the_loop_with_no_further_polls()
    {
        var rig = new Rig();
        var poller = rig.NewPoller();
        var (cb, _) = Listener();
        poller.Subscribe(cb);

        rig.Succeed(await rig.NextPollAsync(), Track);
        var delay = await rig.NextDelayAsync();

        poller.Unsubscribe(cb);
        delay.Tcs.TrySetResult(true); // even if the abandoned timer fires, nothing may poll

        Assert.False(await rig.PollStartsWithin(150));
        Assert.Equal(1, rig.PollCalls);
    }

    [Fact]
    public async Task stop_with_pending_delay_kills_the_loop_even_with_subscribers_still_attached()
    {
        var rig = new Rig();
        var poller = rig.NewPoller();
        var (cb, _) = Listener();
        poller.Subscribe(cb);

        rig.Succeed(await rig.NextPollAsync(), Track);
        var delay = await rig.NextDelayAsync(); // chain parked on the delay

        poller.Stop(); // plugin Unload: nobody unsubscribed, the loop must die anyway
        delay.Tcs.TrySetResult(true); // even if the abandoned timer fires, nothing may poll

        Assert.False(await rig.PollStartsWithin(150));
        Assert.Equal(1, rig.PollCalls);
    }

    [Fact]
    public async Task kick_with_pending_delay_polls_immediately_and_resets_backoff()
    {
        var rig = new Rig();
        var poller = rig.NewPoller();
        var (cb, _) = Listener();
        poller.Subscribe(cb);

        for (var i = 0; i < 3; i++)
        {
            rig.Fail(await rig.NextPollAsync(), new InvalidOperationException());
            if (i < 2)
            {
                (await rig.NextDelayAsync()).Tcs.SetResult(true);
            }
        }
        var backedOff = await rig.NextDelayAsync();
        Assert.Equal(30000, backedOff.Ms);

        poller.Kick(); // delay pending: skip it and poll now — its Tcs is never completed

        var poll = await rig.NextPollAsync();
        rig.Fail(poll, new InvalidOperationException());
        var next = await rig.NextDelayAsync();
        Assert.Equal(5000, next.Ms); // Kick reset failures; one fresh failure stays at baseMs
        Assert.Equal(1, rig.MaxConcurrentPolls);
    }

    [Fact]
    public async Task kick_during_inflight_poll_does_not_start_a_second_chain()
    {
        var rig = new Rig();
        var poller = rig.NewPoller();
        var (cb, seen) = Listener();
        poller.Subscribe(cb);

        var poll = await rig.NextPollAsync(); // in flight, no timer pending
        poller.Kick();
        poller.Kick();

        Assert.False(await rig.PollStartsWithin(150)); // no second chain
        Assert.Equal(1, rig.PollCalls);

        rig.Succeed(poll, Track); // the one chain carries on normally
        var delay = await rig.NextDelayAsync();
        Assert.Equal(5000, delay.Ms);
        Assert.Equal(1, rig.MaxConcurrentPolls);
        Assert.Equal(new PollState[] { new PollState.Data(Track) }, seen.ToArray());
    }

    [Fact]
    public async Task resubscribe_while_poll_in_flight_does_not_stack_chains()
    {
        var rig = new Rig();
        var poller = rig.NewPoller();
        var (a, _) = Listener();
        var (b, seenB) = Listener();

        poller.Subscribe(a);
        var poll = await rig.NextPollAsync(); // in flight: no timer pending
        poller.Unsubscribe(a);                // nothing to cancel; the chain lives on
        poller.Subscribe(b);                  // must ride the existing chain, not start one

        Assert.False(await rig.PollStartsWithin(150));
        Assert.Equal(1, rig.PollCalls);

        rig.Succeed(poll, Track); // the single chain carries on and serves b
        var delay = await rig.NextDelayAsync();
        Assert.Equal(5000, delay.Ms);
        Assert.Equal(1, rig.MaxConcurrentPolls);
        Assert.Equal(new PollState[] { new PollState.Data(Track) }, seenB.ToArray());
    }

    [Fact]
    public async Task throwing_subscriber_breaks_neither_the_loop_nor_other_subscribers()
    {
        var rig = new Rig();
        var poller = rig.NewPoller();
        var (good, seen) = Listener();
        Action<PollState> bad = _ => throw new InvalidOperationException("bad subscriber");

        poller.Subscribe(bad);
        poller.Subscribe(good);

        rig.Succeed(await rig.NextPollAsync(), Track);
        var d1 = await rig.NextDelayAsync(); // loop survived the throwing subscriber
        Assert.Single(seen);

        d1.Tcs.SetResult(true);
        rig.Succeed(await rig.NextPollAsync(), Track);
        await rig.NextDelayAsync();
        Assert.Equal(2, seen.Count); // and keeps delivering
    }
}
