// Defaults to a same-origin path served by the hosting rewrite in
// firebase.json, so solo search needs no env var and no CORS. The rewrite
// targets the `searchYouTube` function, which is NOT deployed today — callers
// must handle failure (see lib/searchMode.ts, which falls back to the bot).
const FUNCTIONS_BASE = import.meta.env.VITE_FUNCTIONS_URL || "/api";

export interface SearchResult {
  videoId: string;
  title: string;
  artist: string;
  thumbnail: string;
  url: string;
  duration: number;
}

export async function searchYouTube(
  query: string,
  signal?: AbortSignal
): Promise<SearchResult[]> {
  const res = await fetch(
    `${FUNCTIONS_BASE}/searchYouTube?q=${encodeURIComponent(query)}&maxResults=10`,
    { signal }
  );
  if (!res.ok) throw new Error("Search failed");
  const data = await res.json();
  return (data.results || []).map((r: Omit<SearchResult, "duration">) => ({
    ...r,
    duration: 0,
  }));
}
