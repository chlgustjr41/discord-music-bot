import type { Track } from "../../types";

export interface PlaylistDoc {
  name: string;
  tracks: Track[];
  createdBy: string;
  createdAt: unknown;
}

export type UnifiedTrackSource =
  | "queue"
  | "history"
  | "playlist"          // existing tracks of the playlist being edited
  | { kind: "other-playlist"; name: string };

export interface UnifiedTrack extends Track {
  source: UnifiedTrackSource;
}
