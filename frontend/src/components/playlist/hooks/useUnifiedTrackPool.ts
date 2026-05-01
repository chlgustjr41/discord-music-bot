import { useState, useCallback, useEffect } from "react";
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

export function useUnifiedTrackPool({
  serverId,
  currentQueue,
  currentTrack,
  editingPlaylistName,
  existingTracks,
  refreshKey = 0,
}: Args) {
  const [pool, setPool] = useState<UnifiedTrack[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
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

    if (currentTrack && !seen.has(currentTrack.url)) {
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
      if (!seen.has(t.url)) {
        seen.add(t.url);
        out.push({ ...t, source: "queue" });
      }
    }

    try {
      const histQ = query(
        collection(db, "servers", serverId, "musicHistory"),
        orderBy("addedAt", "desc"),
        limit(100)
      );
      const histSnap = await getDocs(histQ);
      for (const d of histSnap.docs) {
        const h = d.data() as MusicHistoryEntry;
        if (!seen.has(h.url)) {
          seen.add(h.url);
          out.push({
            title: h.title,
            artist: h.artist,
            url: h.url,
            thumbnail: h.thumbnail,
            duration: h.duration || 0,
            requestedBy: h.requestedBy || "Web User",
            source: "history",
          });
        }
      }
    } catch {
      /* fall through */
    }

    try {
      const plSnap = await getDocs(
        collection(db, "servers", serverId, "playlists")
      );
      for (const d of plSnap.docs) {
        if (d.id === editingPlaylistName) continue;
        const data = d.data() as PlaylistDoc;
        for (const t of data.tracks ?? []) {
          if (t.url && !seen.has(t.url)) {
            seen.add(t.url);
            out.push({
              ...t,
              source: { kind: "other-playlist", name: d.id },
            });
          }
        }
      }
    } catch {
      /* fall through */
    }

    setPool(out);
    setLoading(false);
  }, [serverId, currentQueue, currentTrack, editingPlaylistName, existingTracks]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  return { pool, loading, reload: load };
}
