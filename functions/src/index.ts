import { onRequest } from "firebase-functions/v2/https";
import { defineString } from "firebase-functions/params";

const youtubeApiKey = defineString("YOUTUBE_API_KEY");

export const searchYouTube = onRequest({ cors: true }, async (req, res) => {
  const query = req.query.q as string;
  if (!query) {
    res.status(400).json({ error: "Missing query parameter 'q'" });
    return;
  }

  const maxResults = Math.min(Number(req.query.maxResults) || 10, 20);
  const url = `https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&videoCategoryId=10&maxResults=${maxResults}&q=${encodeURIComponent(query)}&key=${youtubeApiKey.value()}`;

  const response = await fetch(url);
  const data = await response.json();

  if (!response.ok) {
    res.status(response.status).json({ error: data.error?.message || "YouTube API error" });
    return;
  }

  const results = (data.items || []).map((item: any) => ({
    videoId: item.id.videoId,
    title: item.snippet.title,
    artist: item.snippet.channelTitle,
    thumbnail: item.snippet.thumbnails?.medium?.url || "",
    url: `https://www.youtube.com/watch?v=${item.id.videoId}`,
  }));

  res.json({ results });
});
