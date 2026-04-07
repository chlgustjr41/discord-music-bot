import { useState, useEffect } from "react";
import { collection, getDocs, doc, setDoc, deleteDoc, updateDoc, arrayUnion, serverTimestamp } from "firebase/firestore";
import { db } from "../firebase";
import type { Track, CurrentTrack, Playlist } from "../types";

interface Props {
  serverId: string;
  currentQueue: Track[];
  currentTrack: CurrentTrack | null;
}

export function PlaylistManager({ serverId, currentQueue, currentTrack }: Props) {
  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [saveName, setSaveName] = useState("");
  const [expanded, setExpanded] = useState(false);

  const fetchPlaylists = async () => {
    const snap = await getDocs(collection(db, "servers", serverId, "playlists"));
    setPlaylists(snap.docs.map((d) => ({ name: d.id, ...d.data() } as Playlist)));
  };

  useEffect(() => {
    if (expanded) fetchPlaylists();
  }, [expanded, serverId]);

  const savePlaylist = async () => {
    if (!saveName.trim()) return;
    const tracks: Track[] = [];
    if (currentTrack) {
      tracks.push({
        title: currentTrack.title,
        artist: currentTrack.artist,
        url: currentTrack.url,
        thumbnail: currentTrack.thumbnail,
        duration: currentTrack.duration,
        requestedBy: currentTrack.requestedBy,
      });
    }
    tracks.push(...currentQueue);
    if (tracks.length === 0) return;

    await setDoc(doc(db, "servers", serverId, "playlists", saveName.trim()), {
      name: saveName.trim(),
      tracks,
      createdBy: "Web User",
      createdAt: serverTimestamp(),
    });
    setSaveName("");
    fetchPlaylists();
  };

  const loadPlaylist = async (playlist: Playlist) => {
    const tracksToAdd = playlist.tracks.map((t) => ({
      ...t,
      requestedBy: "Web User",
    }));
    for (const t of tracksToAdd) {
      await updateDoc(doc(db, "servers", serverId), {
        queue: arrayUnion(t),
      });
    }
  };

  const deletePlaylist = async (name: string) => {
    await deleteDoc(doc(db, "servers", serverId, "playlists", name));
    fetchPlaylists();
  };

  return (
    <div style={{ padding: "16px 0" }}>
      <h3 onClick={() => setExpanded(!expanded)} style={{ cursor: "pointer" }}>
        Saved Playlists {expanded ? "\u25be" : "\u25b8"}
      </h3>

      {expanded && (
        <>
          <div style={{ display: "flex", gap: "8px", marginBottom: "12px" }}>
            <input
              type="text"
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
              placeholder="Playlist name"
              style={{ flex: 1 }}
            />
            <button onClick={savePlaylist} disabled={!saveName.trim()}>Save Current</button>
          </div>

          {playlists.length === 0 ? (
            <p style={{ opacity: 0.5 }}>No saved playlists yet.</p>
          ) : (
            <ul style={{ listStyle: "none", padding: 0 }}>
              {playlists.map((p) => (
                <li key={p.name} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px", borderBottom: "1px solid #333" }}>
                  <div>
                    <strong>{p.name}</strong>
                    <span style={{ marginLeft: "8px", opacity: 0.5 }}>{p.tracks.length} tracks</span>
                  </div>
                  <div style={{ display: "flex", gap: "8px" }}>
                    <button onClick={() => loadPlaylist(p)}>Load</button>
                    <button onClick={() => deletePlaylist(p.name)}>Delete</button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
