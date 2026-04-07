import { useState, useEffect } from "react";
import type { CurrentTrack } from "../types";

interface Props {
  track: CurrentTrack | null;
  isPaused: boolean;
}

export function NowPlaying({ track, isPaused }: Props) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!track || isPaused) return;
    const started = new Date(track.startedAt).getTime();
    const interval = setInterval(() => {
      setElapsed(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, [track, isPaused]);

  if (!track) {
    return (
      <div style={{ padding: "24px", textAlign: "center" }}>
        <p>Nothing is playing</p>
      </div>
    );
  }

  const progress = Math.min(elapsed / track.duration, 1);
  const formatTime = (s: number) => {
    const m = Math.floor(s / 60);
    return `${m}:${String(s % 60).padStart(2, "0")}`;
  };

  return (
    <div style={{ padding: "16px", display: "flex", gap: "16px", alignItems: "center" }}>
      {track.thumbnail && (
        <img src={track.thumbnail} alt="" style={{ width: "80px", height: "80px", borderRadius: "8px" }} />
      )}
      <div style={{ flex: 1 }}>
        <h3 style={{ margin: 0 }}>{track.title}</h3>
        <p style={{ margin: "4px 0", opacity: 0.7 }}>{track.artist}</p>
        <div style={{ background: "#333", borderRadius: "4px", height: "6px", marginTop: "8px" }}>
          <div style={{ background: "#1DB954", borderRadius: "4px", height: "100%", width: `${progress * 100}%`, transition: "width 1s linear" }} />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8rem", marginTop: "4px" }}>
          <span>{formatTime(Math.min(elapsed, track.duration))}</span>
          <span>{formatTime(track.duration)}</span>
        </div>
      </div>
    </div>
  );
}
