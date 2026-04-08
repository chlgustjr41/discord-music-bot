import { useEffect, useRef, useState, useCallback } from "react";
import { toast } from "sonner";
import type { ServerState, Track } from "../types";

/* ------------------------------------------------------------------ */
/*  Expandable track-list (used as toast description)                 */
/* ------------------------------------------------------------------ */

function ExpandableTrackList({
  tracks,
}: {
  tracks: { name: string; position?: number }[];
}) {
  const [expanded, setExpanded] = useState(false);
  const preview = tracks[0]?.name ?? "";
  const more = tracks.length - 1;

  if (!expanded) {
    return (
      <p className="truncate">
        {preview}
        {more > 0 && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setExpanded(true);
            }}
            className="ml-1 underline opacity-80 hover:opacity-100"
          >
            +{more} more
          </button>
        )}
      </p>
    );
  }

  return (
    <ul className="space-y-0.5 max-h-48 overflow-y-auto">
      {tracks.map((t, i) => (
        <li key={i} className="truncate">
          {t.position != null && (
            <span className="opacity-50 mr-1">#{t.position}</span>
          )}
          {t.name}
        </li>
      ))}
    </ul>
  );
}

/* ------------------------------------------------------------------ */
/*  Queue diff helpers                                                */
/* ------------------------------------------------------------------ */

function urlCounts(queue: Track[]): Map<string, number> {
  const m = new Map<string, number>();
  for (const t of queue) {
    m.set(t.url, (m.get(t.url) ?? 0) + 1);
  }
  return m;
}

function addedTracks(older: Track[], newer: Track[]): Track[] {
  const oldCounts = urlCounts(older);
  const result: Track[] = [];
  const seen = new Map<string, number>();
  for (const t of newer) {
    const s = seen.get(t.url) ?? 0;
    seen.set(t.url, s + 1);
    if (s + 1 > (oldCounts.get(t.url) ?? 0)) {
      result.push(t);
    }
  }
  return result;
}

function removedTracks(older: Track[], newer: Track[]): Track[] {
  return addedTracks(newer, older);
}

function detectMove(
  older: Track[],
  newer: Track[]
): { track: Track; to: number } | null {
  if (older.length !== newer.length || older.length < 2) return null;

  const diffs: number[] = [];
  for (let i = 0; i < older.length; i++) {
    if (older[i].url !== newer[i].url) diffs.push(i);
  }
  if (diffs.length < 2) return null;

  const lo = diffs[0];
  const hi = diffs[diffs.length - 1];
  if (hi - lo + 1 !== diffs.length) return null;
  if (diffs.length > Math.ceil(older.length / 2)) return null;

  if (newer[lo].url === older[hi].url) return { track: newer[lo], to: lo };
  if (newer[hi].url === older[lo].url) return { track: newer[hi], to: hi };
  return null;
}

function sourceLabel(tracks: Track[]): string {
  const users = new Set(tracks.map((t) => t.requestedBy));
  if (users.size === 1) {
    const user = users.values().next().value!;
    return user === "Web User" ? "via Web" : `by ${user}`;
  }
  return "";
}

function formatTime(s: number): string {
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

/* ------------------------------------------------------------------ */
/*  Batch toast helper                                                */
/* ------------------------------------------------------------------ */

function showBatchToast(
  title: string,
  tracks: { name: string; position?: number }[]
) {
  if (tracks.length === 0) return;
  toast(title, {
    description: <ExpandableTrackList tracks={tracks} />,
    duration: 5000,
  });
}

/* ------------------------------------------------------------------ */
/*  Hook                                                              */
/* ------------------------------------------------------------------ */

export interface LogEntry {
  id: number;
  message: string;
  timestamp: Date;
}

const BATCH_DEBOUNCE = 1500;
let logIdCounter = 0;

export function useActivityToasts(state: ServerState | null) {
  const prevRef = useRef<ServerState | null>(null);
  const initialized = useRef(false);
  const [logEntries, setLogEntries] = useState<LogEntry[]>([]);

  const addLog = useCallback((message: string) => {
    setLogEntries((prev) => [
      { id: ++logIdCounter, message, timestamp: new Date() },
      ...prev,
    ]);
  }, []);

  const pendingAdds = useRef<Track[]>([]);
  const pendingQueue = useRef<Track[]>([]);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const addLogRef = useRef(addLog);
  addLogRef.current = addLog;

  const flushPending = useCallback(() => {
    debounceTimer.current = null;
    const tracks = pendingAdds.current;
    const queue = pendingQueue.current;
    pendingAdds.current = [];
    pendingQueue.current = [];
    if (tracks.length === 0) return;

    const src = sourceLabel(tracks);

    if (tracks.length === 1) {
      const msg = `Added to queue${src ? ` ${src}` : ""}: ${tracks[0].title}`;
      toast(msg, { duration: 3000 });
      addLogRef.current(msg);
    } else {
      const msg = `${tracks.length} tracks added to queue${src ? ` — ${src}` : ""}`;
      showBatchToast(
        msg,
        tracks.map((t) => ({
          name: t.title,
          position: queue.findIndex((q) => q.url === t.url) + 1 || undefined,
        }))
      );
      addLogRef.current(msg + ": " + tracks.map((t) => t.title).join(", "));
    }
  }, []);

  useEffect(() => {
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
    };
  }, []);

  useEffect(() => {
    if (!state) return;

    if (!initialized.current) {
      initialized.current = true;
      prevRef.current = state;
      return;
    }

    const prev = prevRef.current;
    prevRef.current = state;
    if (!prev) return;

    const oldLen = prev.queue.length;
    const newLen = state.queue.length;

    // --- Queue: tracks added ---
    if (newLen > oldLen) {
      const added = addedTracks(prev.queue, state.queue);

      if (added.length > 1) {
        // Batch arrival (web) — flush pending singles first
        if (pendingAdds.current.length > 0) {
          if (debounceTimer.current) clearTimeout(debounceTimer.current);
          flushPending();
        }
        const src = sourceLabel(added);
        const msg = `${added.length} tracks added to queue${src ? ` — ${src}` : ""}`;
        showBatchToast(
          msg,
          added.map((t) => ({
            name: t.title,
            position:
              state.queue.findIndex((q) => q.url === t.url) + 1 || undefined,
          }))
        );
        addLog(msg + ": " + added.map((t) => t.title).join(", "));
      } else if (added.length === 1) {
        // Single track — buffer for possible rapid sequence
        pendingAdds.current.push(added[0]);
        pendingQueue.current = state.queue;
        if (debounceTimer.current) clearTimeout(debounceTimer.current);
        debounceTimer.current = setTimeout(flushPending, BATCH_DEBOUNCE);
      }
    }

    // --- Queue: tracks removed ---
    else if (newLen < oldLen) {
      const shrunk = oldLen - newLen;
      const wasPlayNext =
        shrunk === 1 &&
        state.currentTrack &&
        prev.queue[0] &&
        state.currentTrack.url === prev.queue[0].url;

      if (!wasPlayNext) {
        const removed = removedTracks(prev.queue, state.queue);
        if (removed.length > 0) {
          if (newLen === 0 && oldLen > 1 && removed.length === oldLen) {
            toast("Queue cleared", { duration: 3000 });
            addLog("Queue cleared");
          } else if (removed.length === 1) {
            const msg = `Removed from queue: ${removed[0].title}`;
            toast(msg, { duration: 3000 });
            addLog(msg);
          } else {
            const msg = `${removed.length} tracks removed from queue`;
            showBatchToast(msg, removed.map((t) => ({ name: t.title })));
            addLog(msg + ": " + removed.map((t) => t.title).join(", "));
          }
        }
      }
    }

    // --- Queue: reordered ---
    else if (
      newLen === oldLen &&
      newLen > 1 &&
      state.queue.some((t, i) => t.url !== prev.queue[i]?.url)
    ) {
      const move = detectMove(prev.queue, state.queue);
      if (move) {
        const msg = `Moved "${move.track.title}" to #${move.to + 1}`;
        toast(msg, { duration: 3000 });
        addLog(msg);
      } else {
        toast("Queue shuffled", { duration: 3000 });
        addLog("Queue shuffled");
      }
    }

    // --- Pause / Resume ---
    if (prev.isPaused !== state.isPaused) {
      if (state.isPaused) {
        toast("Playback paused", { duration: 2000 });
        addLog("Playback paused");
      } else if (state.currentTrack) {
        toast("Playback resumed", { duration: 2000 });
        addLog("Playback resumed");
      }
    }

    // --- Volume ---
    if (prev.volume !== state.volume) {
      const msg = `Volume: ${state.volume}%`;
      toast(msg, { duration: 2000 });
      addLog(msg);
    }

    // --- Discord notify toggle ---
    if ((prev.discordNotify !== false) !== (state.discordNotify !== false)) {
      const msg = state.discordNotify !== false
        ? "Discord notifications enabled"
        : "Discord notifications disabled";
      toast(msg, { duration: 2000 });
      addLog(msg);
    }

    // --- Loop mode ---
    if (prev.loopMode !== state.loopMode) {
      const labels: Record<string, string> = {
        off: "Loop off",
        track: "Looping current track",
        queue: "Looping queue",
      };
      const msg = labels[state.loopMode] || `Loop: ${state.loopMode}`;
      toast(msg, { duration: 2000 });
      addLog(msg);
    }

    // --- Seek ---
    if (
      state.currentTrack &&
      prev.currentTrack &&
      state.currentTrack.url === prev.currentTrack.url &&
      state.currentTrack.startedAt !== prev.currentTrack.startedAt
    ) {
      const started = new Date(state.currentTrack.startedAt).getTime();
      const position = Math.max(0, Math.floor((Date.now() - started) / 1000));
      const msg = `Seeked to ${formatTime(position)}`;
      toast(msg, { duration: 2000 });
      addLog(msg);
    }

    // --- Now playing ---
    if (
      state.currentTrack &&
      prev.currentTrack?.url !== state.currentTrack.url
    ) {
      const msg = `Now playing: ${state.currentTrack.title}`;
      toast(msg, { duration: 3000 });
      addLog(msg);
    }

    // --- Playback stopped ---
    if (prev.currentTrack && !state.currentTrack && !state.isPlaying) {
      toast("Playback stopped", { duration: 2000 });
      addLog("Playback stopped");
    }
  }, [state, flushPending, addLog]);

  return logEntries;
}
