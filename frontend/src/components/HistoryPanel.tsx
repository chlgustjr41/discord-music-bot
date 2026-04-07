import { useState, useEffect } from "react";
import { collection, getDocs, query, orderBy, limit, doc, updateDoc, arrayUnion } from "firebase/firestore";
import { db } from "../firebase";
import type { HistorySession } from "../types";

interface Props {
  serverId: string;
}

export function HistoryPanel({ serverId }: Props) {
  const [sessions, setSessions] = useState<HistorySession[]>([]);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!expanded) return;
    const q = query(
      collection(db, "servers", serverId, "history"),
      orderBy("startedAt", "desc"),
      limit(5)
    );
    getDocs(q).then((snap) => {
      setSessions(snap.docs.map((d) => ({ id: d.id, ...d.data() } as HistorySession)));
    });
  }, [expanded, serverId]);

  const requeueSession = async (session: HistorySession) => {
    for (const track of session.tracks) {
      await updateDoc(doc(db, "servers", serverId), {
        queue: arrayUnion({
          title: track.title,
          artist: track.artist,
          url: track.url,
          thumbnail: track.thumbnail,
          duration: track.duration,
          requestedBy: "Web User",
        }),
      });
    }
  };

  return (
    <div style={{ padding: "16px 0" }}>
      <h3 onClick={() => setExpanded(!expanded)} style={{ cursor: "pointer" }}>
        History {expanded ? "\u25be" : "\u25b8"}
      </h3>

      {expanded && (
        sessions.length === 0 ? (
          <p style={{ opacity: 0.5 }}>No history yet.</p>
        ) : (
          <div>
            {sessions.map((s) => (
              <div key={s.id} style={{ marginBottom: "16px", padding: "12px", border: "1px solid #333", borderRadius: "8px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <strong>{s.startedAt?.slice(0, 10) || "Unknown date"}</strong>
                  <button onClick={() => requeueSession(s)}>Re-queue All</button>
                </div>
                <ul style={{ paddingLeft: "20px", marginTop: "8px" }}>
                  {s.tracks.slice(0, 5).map((t, i) => (
                    <li key={i} style={{ opacity: 0.7 }}>{t.title} — {t.artist}</li>
                  ))}
                  {s.tracks.length > 5 && (
                    <li style={{ opacity: 0.5 }}>...and {s.tracks.length - 5} more</li>
                  )}
                </ul>
              </div>
            ))}
          </div>
        )
      )}
    </div>
  );
}
