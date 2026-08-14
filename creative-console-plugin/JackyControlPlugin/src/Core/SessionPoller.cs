namespace Loupedeck.JackyControlPlugin;

using System;
using System.Collections.Generic;
using System.Threading.Tasks;

/// <summary>Poll loop outcome, pattern-matchable and value-equal.</summary>
public abstract record PollState
{
    public sealed record Data(NowPlaying NowPlaying) : PollState;
    public sealed record Offline : PollState;
    public sealed record Unauthorized : PollState;
    public sealed record Unconfigured : PollState;
}

/// <summary>
/// Port of poller.ts: single shared now-playing poll loop. Runs only while
/// subscribed; backs off from baseMs to maxMs after consecutive failures.
/// The delay fn is injectable so tests drive time by completing tasks
/// manually; production uses Task.Delay. All state transitions happen under
/// one lock because Subscribe/Unsubscribe/Kick (SDK events) race the loop.
/// </summary>
public sealed class SessionPoller
{
    private const Int32 BackoffAfterFailures = 3;

    /// <summary>
    /// One tick chain. <see cref="Wake"/> is non-null iff the chain is parked
    /// on a delay — the analog of poller.ts's pending timer; completing it
    /// skips the remaining wait. The loop exits whenever it observes that
    /// <c>_chain</c> no longer references it (killed by the last unsubscribe).
    /// </summary>
    private sealed class Chain
    {
        public TaskCompletionSource<Boolean> Wake;
    }

    private readonly Object _gate = new();
    private readonly HashSet<Action<PollState>> _subs = new();
    private readonly Func<Task<NowPlaying>> _poll;
    private readonly Func<Int32, Task> _delay;
    private readonly Int32 _baseMs;
    private readonly Int32 _maxMs;

    /// <summary>True iff a tick chain is alive (poll in flight or delay
    /// pending) — poller.ts's `polling` flag. Guards against starting a
    /// second chain when a resubscribe (or Kick) happens while a poll is
    /// still in flight: at most one chain ever exists.</summary>
    private Chain _chain;

    /// <summary>Deliberately persists across unsubscribe/resubscribe: the
    /// server was just failing, so the backoff state is still real.</summary>
    private Int32 _failures;

    public SessionPoller(Func<Task<NowPlaying>> poll, Func<Int32, Task> delay = null, Int32 baseMs = 5000, Int32 maxMs = 30000)
    {
        this._poll = poll;
        this._delay = delay ?? (ms => Task.Delay(ms));
        this._baseMs = baseMs;
        this._maxMs = maxMs;
    }

    public void Subscribe(Action<PollState> cb)
    {
        Chain start = null;
        lock (this._gate)
        {
            this._subs.Add(cb);
            if (this._chain == null)
            {
                this._chain = start = new Chain();
            }
        }
        if (start != null)
        {
            _ = this.RunChainAsync(start);
        }
    }

    public void Unsubscribe(Action<PollState> cb)
    {
        TaskCompletionSource<Boolean> wake = null;
        lock (this._gate)
        {
            this._subs.Remove(cb);
            if (this._subs.Count == 0 && this._chain != null && this._chain.Wake != null)
            {
                // Delay pending: kill the chain now so the abandoned timer can
                // never poll again. (If a poll is in flight instead, the chain
                // notices the empty subscriber set after it completes.)
                wake = this._chain.Wake;
                this._chain = null;
            }
        }
        wake?.TrySetResult(true);
    }

    /// <summary>Reset backoff and poll again now (e.g. after settings change).
    /// Chain invariant ("at most one tick chain"): if a delay is pending we
    /// skip it — the chain continues, just sooner. If a poll is in flight
    /// (chain alive, no Wake) we must NOT start anything, or we'd have a
    /// second chain; resetting failures is enough, because the in-flight tick
    /// reschedules at baseMs once failures is 0.</summary>
    public void Kick()
    {
        TaskCompletionSource<Boolean> wake = null;
        Chain start = null;
        lock (this._gate)
        {
            this._failures = 0;
            if (this._chain != null)
            {
                wake = this._chain.Wake; // null while a poll is in flight → no-op
            }
            else if (this._subs.Count > 0)
            {
                this._chain = start = new Chain();
            }
        }
        wake?.TrySetResult(true);
        if (start != null)
        {
            _ = this.RunChainAsync(start);
        }
    }

    /// <summary>Plugin shutdown: kill the loop regardless of subscribers, so a
    /// delay-parked chain can never tick again against disposed services
    /// (Unload runs before HttpClient.Dispose and action OnUnloads are not
    /// guaranteed to have fired). Clearing <c>_chain</c> under the lock makes
    /// any surviving chain observe it is no longer the live one and exit;
    /// completing a pending Wake makes a parked chain observe that now
    /// rather than after the abandoned delay fires.</summary>
    public void Stop()
    {
        TaskCompletionSource<Boolean> wake = null;
        lock (this._gate)
        {
            if (this._chain != null)
            {
                wake = this._chain.Wake; // null while a poll is in flight — that chain exits after the poll
                this._chain = null;
            }
        }
        wake?.TrySetResult(true);
    }

    private void Emit(PollState state)
    {
        Action<PollState>[] subs;
        lock (this._gate)
        {
            subs = new Action<PollState>[this._subs.Count];
            this._subs.CopyTo(subs);
        }
        foreach (var cb in subs)
        {
            try
            {
                cb(state);
            }
            catch
            {
                // subscriber errors must not break polling
            }
        }
    }

    private async Task RunChainAsync(Chain chain)
    {
        while (true)
        {
            PollState state;
            try
            {
                var data = await this._poll().ConfigureAwait(false);
                lock (this._gate) { this._failures = 0; }
                state = new PollState.Data(data);
            }
            catch (ControlApiException ex) when (ex.Status == 401)
            {
                lock (this._gate) { this._failures++; }
                state = new PollState.Unauthorized();
            }
            catch (ControlApiException ex) when (ex.Status == 0)
            {
                lock (this._gate) { this._failures++; }
                state = new PollState.Unconfigured();
            }
            catch
            {
                lock (this._gate) { this._failures++; }
                state = new PollState.Offline();
            }
            this.Emit(state);

            Int32 ms;
            TaskCompletionSource<Boolean> wake;
            lock (this._gate)
            {
                if (!ReferenceEquals(this._chain, chain))
                {
                    return; // killed while we were polling
                }
                if (this._subs.Count == 0)
                {
                    this._chain = null;
                    return;
                }
                ms = this._failures >= BackoffAfterFailures ? this._maxMs : this._baseMs;
                wake = new TaskCompletionSource<Boolean>(TaskCreationOptions.RunContinuationsAsynchronously);
                chain.Wake = wake;
            }
            await Task.WhenAny(this._delay(ms), wake.Task).ConfigureAwait(false);
            lock (this._gate)
            {
                chain.Wake = null;
                if (!ReferenceEquals(this._chain, chain))
                {
                    return; // last unsubscribe killed us during the delay
                }
                if (this._subs.Count == 0)
                {
                    this._chain = null;
                    return;
                }
            }
        }
    }
}
