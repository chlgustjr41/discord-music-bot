import { useState } from "react";
import { doc, updateDoc, arrayUnion } from "firebase/firestore";
import { db } from "../firebase";
import { searchYouTube, type SearchResult } from "../services/api";

interface Props {
  serverId: string;
}

export function SearchPanel({ serverId }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSearch = async () => {
    if (!query.trim()) return;
    setLoading(true);
    setError("");
    try {
      const data = await searchYouTube(query);
      setResults(data);
    } catch {
      setError("Search failed. Try again.");
    }
    setLoading(false);
  };

  const addToQueue = async (result: SearchResult) => {
    await updateDoc(doc(db, "servers", serverId), {
      queue: arrayUnion({
        title: result.title,
        artist: result.artist,
        url: result.url,
        thumbnail: result.thumbnail,
        duration: 0,  // Duration resolved by bot when playing
        requestedBy: "Web User",
      }),
    });
    setResults((prev) => prev.filter((r) => r.videoId !== result.videoId));
  };

  return (
    <div style={{ padding: "16px 0" }}>
      <h3>Search</h3>
      <div style={{ display: "flex", gap: "8px" }}>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search YouTube for music..."
          style={{ flex: 1 }}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
        />
        <button onClick={handleSearch} disabled={loading}>
          {loading ? "Searching..." : "Search"}
        </button>
      </div>

      {error && <p style={{ color: "red" }}>{error}</p>}

      {results.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0, marginTop: "12px" }}>
          {results.map((r) => (
            <li key={r.videoId} style={{ display: "flex", alignItems: "center", gap: "8px", padding: "8px", borderBottom: "1px solid #333" }}>
              {r.thumbnail && (
                <img src={r.thumbnail} alt="" style={{ width: "60px", height: "45px", borderRadius: "4px" }} />
              )}
              <div style={{ flex: 1 }}>
                <strong>{r.title}</strong>
                <br />
                <span style={{ fontSize: "0.85rem", opacity: 0.7 }}>{r.artist}</span>
              </div>
              <button onClick={() => addToQueue(r)}>+ Add</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
