export interface Track {
  title: string;
  artist: string;
  url: string;
  thumbnail: string;
  duration: number;
  requestedBy: string;
}

export interface CurrentTrack extends Track {
  startedAt: string;
}

export interface ServerState {
  sessionCode: string | null;
  currentTrack: CurrentTrack | null;
  queue: Track[];
  isPlaying: boolean;
  isPaused: boolean;
  loopMode: "off" | "track" | "queue";
  volume: number;
  voiceChannelId: string | null;
  textChannelId: string | null;
  idleTimeoutMinutes: number;
}

export interface Playlist {
  name: string;
  tracks: Track[];
  createdBy: string;
  createdAt: string;
}

export interface HistorySession {
  id: string;
  startedAt: string;
  endedAt: string;
  tracks: (Track & { playedAt: string })[];
}

export interface SessionCodeDoc {
  serverId: string;
  createdAt: string;
}
