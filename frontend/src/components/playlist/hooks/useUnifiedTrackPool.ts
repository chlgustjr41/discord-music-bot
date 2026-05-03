import { useState, useCallback, useEffect, useMemo } from "react";
import {
  collection,
  getDocs,
  query,
  orderBy,
  limit,
} from "firebase/firestore";
import { db } from "../../../firebase";
import type { Track, CurrentTrack, MusicHistoryEntry } from "../../../types";
import type { UnifiedTrack, PlaylistDoc } from "../types";

interface Args {
  serverId: string;
  currentQueue: Track[];
  currentTrack: CurrentTrack | null;
  /** When editing: the playlist whose tracks should appear first and which should be excluded from "other playlists" pool. */
  editingPlaylistName?: string;
  existingTracks?: Track[];
  /** Re-fetch trigger; bump to force refresh. */
  refreshKey?: number;
}

interface FetchedTracks {
  history: UnifiedTrack[];
  otherPlaylists: UnifiedTrack[];
}

const EMPTY_FETCHED: FetchedTracks = { history: [], otherPlaylists: [] };

// Fetched sources (history + other-playlists) depend on stable inputs only.
// Live inputs (currentQueue, currentTrack, existingTracks) are merged in-memory
// to avoid a Firestore round-trip per server-doc snapshot.
export function useUnifiedTrackPool({
  serverId,
  currentQueue,
  currentTrack,
  editingPlaylistName,
  existingTracks,
  refreshKey = 0,
}: Args) {
  const [fetched, setFetched] = useState<FetchedTracks>(EMPTY_FETCHED);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    const next: FetchedTracks = { history: [], otherPlaylists: [] };

    try {
      const histQ = query(
        collection(db, "servers", serverId, "musicHistory"),
        orderBy("addedAt", "desc"),
        limit(100)
      );
      const histSnap = await getDocs(histQ);
      for (const d of histSnap.docs) {
        const h = d.data() as MusicHistoryEntry;
        next.history.push({
          title: h.title,
          artist: h.artist,
          url: h.url,
          thumbnail: h.thumbnail,
          duration: h.duration || 0,
          requestedBy: h.requestedBy || "Web User",
          source: "history",
        });
      }
    } catch (e) {
      console.warn("useUnifiedTrackPool: musicHistory fetch failed", e);
    }

    try {
      const plSnap = await getDocs(
        collection(db, "servers", serverId, "playlists")
      );
      for (const d of plSnap.docs) {
        if (d.id === editingPlaylistName) continue;
        const data = d.data() as PlaylistDoc;
        for (const t of data.tracks ?? []) {
          next.otherPlaylists.push({
            ...t,
            source: { kind: "other-playlist", name: d.id },
          });
        }
      }
    } catch (e) {
      console.warn("useUnifiedTrackPool: playlists fetch failed", e);
    }

    setFetched(next);
    setLoading(false);
  }, [serverId, editingPlaylistName]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  // Merge order matches the spec: existing playlist → current track → queue → history → other playlists.
  // First-source-wins by URL.
  const pool = useMemo<UnifiedTrack[]>(() => {
    const seen = new Set<string>();
    const out: UnifiedTrack[] = [];

    if (existingTracks) {
      for (const t of existingTracks) {
        if (t.url && !seen.has(t.url)) {
          seen.add(t.url);
          out.push({ ...t, source: "playlist" });
        }
      }
    }

    if (currentTrack && currentTrack.url && !seen.has(currentTrack.url)) {
      seen.add(currentTrack.url);
      out.push({
        title: currentTrack.title,
        artist: currentTrack.artist,
        url: currentTrack.url,
        thumbnail: currentTrack.thumbnail,
        duration: currentTrack.duration,
        requestedBy: currentTrack.requestedBy,
        source: "queue",
      });
    }

    for (const t of currentQueue) {
      if (t.url && !seen.has(t.url)) {
        seen.add(t.url);
        out.push({ ...t, source: "queue" });
      }
    }

    for (const t of fetched.history) {
      if (t.url && !seen.has(t.url)) {
        seen.add(t.url);
        out.push(t);
      }
    }

    for (const t of fetched.otherPlaylists) {
      if (t.url && !seen.has(t.url)) {
        seen.add(t.url);
        out.push(t);
      }
    }

    return out;
  }, [existingTracks, currentTrack, currentQueue, fetched]);

  return { pool, loading, reload: load };
}
