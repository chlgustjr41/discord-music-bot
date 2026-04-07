import { doc, updateDoc, arrayUnion } from "firebase/firestore";
import { db } from "../firebase";
import type { ServerState } from "../types";

interface Props {
  state: ServerState;
  serverId: string;
}

export function PlaybackControls({ state, serverId }: Props) {
  const ref = doc(db, "servers", serverId);

  const togglePause = () => updateDoc(ref, { isPaused: !state.isPaused });

  const skip = () => updateDoc(ref, { currentTrack: null, isPlaying: true });

  const shuffle = () => {
    const shuffled = [...state.queue].sort(() => Math.random() - 0.5);
    updateDoc(ref, { queue: shuffled });
  };

  const cycleLoop = () => {
    const cycle: Record<string, string> = { off: "track", track: "queue", queue: "off" };
    updateDoc(ref, { loopMode: cycle[state.loopMode] });
  };

  const setVolume = (vol: number) => updateDoc(ref, { volume: vol });

  const loopLabel: Record<string, string> = { off: "Loop: Off", track: "Loop: Track", queue: "Loop: Queue" };

  return (
    <div style={{ display: "flex", gap: "12px", alignItems: "center", padding: "12px 0", flexWrap: "wrap" }}>
      <button onClick={togglePause}>
        {state.isPaused ? "Resume" : "Pause"}
      </button>
      <button onClick={skip} disabled={!state.isPlaying}>Skip</button>
      <button onClick={shuffle} disabled={state.queue.length < 2}>Shuffle</button>
      <button onClick={cycleLoop}>{loopLabel[state.loopMode]}</button>

      <div style={{ display: "flex", alignItems: "center", gap: "8px", marginLeft: "auto" }}>
        <span style={{ fontSize: "0.85rem" }}>Vol: {state.volume}%</span>
        <input
          type="range"
          min={0}
          max={100}
          value={state.volume}
          onChange={(e) => setVolume(Number(e.target.value))}
          style={{ width: "120px" }}
        />
      </div>
    </div>
  );
}
