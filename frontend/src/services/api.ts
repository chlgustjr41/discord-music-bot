const FUNCTIONS_BASE = import.meta.env.VITE_FUNCTIONS_URL || "";

export interface SearchResult {
  videoId: string;
  title: string;
  artist: string;
  thumbnail: string;
  url: string;
}

export async function searchYouTube(query: string): Promise<SearchResult[]> {
  const res = await fetch(
    `${FUNCTIONS_BASE}/searchYouTube?q=${encodeURIComponent(query)}&maxResults=10`
  );
  if (!res.ok) throw new Error("Search failed");
  const data = await res.json();
  return data.results;
}
