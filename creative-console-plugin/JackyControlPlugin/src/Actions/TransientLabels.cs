namespace Loupedeck.JackyControlPlugin
{
    using System;
    using System.Collections.Generic;
    using System.Threading.Tasks;

    /// <summary>
    /// Transient per-key result labels ("✓", "Just posted", "Joined AB12")
    /// shared by every action that answers on the key itself. Deadlines are
    /// Environment.TickCount64 (monotonic), not wall-clock: a system clock
    /// adjustment must not stick a label. The clock is injectable for tests
    /// only — production callers use the default.
    /// </summary>
    public sealed class TransientLabels
    {
        private readonly Object _gate = new();
        private readonly Dictionary<String, (String Label, Int64 Until)> _labels = new();
        private readonly Int32 _showMs;
        private readonly Func<Int64> _now;

        public TransientLabels(Int32 showMs = 3000, Func<Int64> now = null)
        {
            this._showMs = showMs;
            this._now = now ?? (() => Environment.TickCount64);
        }

        /// <summary>Shows a label for the key and schedules the revert:
        /// <paramref name="repaint"/> runs once immediately (label on) and once
        /// after the label expires (label off).</summary>
        public void Set(String key, String label, Action repaint)
        {
            lock (this._gate)
            {
                this._labels[key ?? ""] = (label, this._now() + this._showMs);
            }
            repaint();
            _ = Task.Delay(this._showMs).ContinueWith(_ => repaint());
        }

        /// <summary>The label currently in force for the key, or null once the
        /// deadline has passed (or nothing was ever set).</summary>
        public String Current(String key)
        {
            lock (this._gate)
            {
                return this._labels.TryGetValue(key ?? "", out var entry) && this._now() < entry.Until
                    ? entry.Label
                    : null;
            }
        }
    }
}
