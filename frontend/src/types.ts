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

export interface SearchResult {
  videoId: string;
  title: string;
  artist: string;
  url: string;
  thumbnail: string;
  duration: number;
}

export interface KnownVoiceChannel {
  id: string;
  name: string;
}

export interface ServerState {
  sessionCode: string | null;
  knownVoiceChannels?: KnownVoiceChannel[];
  summonRequest?: { channelId: string } | null;
  currentTrack: CurrentTrack | null;
  queue: Track[];
  isPlaying: boolean;
  isPaused: boolean;
  loopMode: "off" | "track" | "queue";
  volume: number;
  voiceChannelId: string | null;
  voiceChannelName?: string;
  textChannelId: string | null;
  idleTimeoutMinutes: number;
  seekPosition?: number | null;
  discordNotify?: boolean;
  searchQuery?: string | null;
  searchResults?: SearchResult[];
  searchPlaylistName?: string | null;
  serverName?: string;
  serverIcon?: string;
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

export interface CommandHistoryEntry {
  id: string;
  command: string;
  args: string;
  user: string;
  userId: string;
  timestamp: unknown; // Firestore Timestamp
  callCount: number;
}

export interface MusicHistoryEntry {
  id: string;
  title: string;
  artist: string;
  url: string;
  thumbnail: string;
  duration: number;
  requestedBy: string;
  addedAt: unknown; // Firestore Timestamp
  playCount: number;
}

export interface SessionCodeDoc {
  serverId: string;
  createdAt: string;
}
