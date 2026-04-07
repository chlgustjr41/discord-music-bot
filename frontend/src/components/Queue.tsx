import { doc, updateDoc } from "firebase/firestore";
import { db } from "../firebase";
import type { Track } from "../types";

interface Props {
  queue: Track[];
  serverId: string;
}

export function Queue({ queue, serverId }: Props) {
  const removeTrack = async (index: number) => {
    const updated = [...queue];
    updated.splice(index, 1);
    await updateDoc(doc(db, "servers", serverId), { queue: updated });
  };

  const moveTrack = async (from: number, to: number) => {
    const updated = [...queue];
    const [track] = updated.splice(from, 1);
    updated.splice(to, 0, track);
    await updateDoc(doc(db, "servers", serverId), { queue: updated });
  };

  if (queue.length === 0) {
    return <p style={{ textAlign: "center", opacity: 0.5 }}>Queue is empty</p>;
  }

  return (
    <div>
      <h3>Queue ({queue.length} tracks)</h3>
      <ul style={{ listStyle: "none", padding: 0 }}>
        {queue.map((track, i) => {
          const mins = Math.floor(track.duration / 60);
          const secs = track.duration % 60;
          return (
            <li key={`${track.url}-${i}`} style={{ display: "flex", alignItems: "center", padding: "8px", gap: "8px", borderBottom: "1px solid #333" }}>
              <span style={{ width: "30px", textAlign: "center", opacity: 0.5 }}>{i + 1}</span>
              {track.thumbnail && (
                <img src={track.thumbnail} alt="" style={{ width: "40px", height: "40px", borderRadius: "4px" }} />
              )}
              <div style={{ flex: 1 }}>
                <strong>{track.title}</strong>
                <br />
                <span style={{ fontSize: "0.85rem", opacity: 0.7 }}>{track.artist} — {mins}:{String(secs).padStart(2, "0")}</span>
              </div>
              <button onClick={() => i > 0 && moveTrack(i, i - 1)} disabled={i === 0} title="Move up">
                &uarr;
              </button>
              <button onClick={() => i < queue.length - 1 && moveTrack(i, i + 1)} disabled={i === queue.length - 1} title="Move down">
                &darr;
              </button>
              <button onClick={() => removeTrack(i)} title="Remove">
                &times;
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
