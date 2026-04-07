import { useParams, useNavigate } from "react-router-dom";
import { useServerState } from "../hooks/useServerState";
import { NowPlaying } from "./NowPlaying";
import { Queue } from "./Queue";
import { PlaybackControls } from "./PlaybackControls";
import { SearchPanel } from "./SearchPanel";
import { PlaylistManager } from "./PlaylistManager";
import { HistoryPanel } from "./HistoryPanel";

export function Dashboard() {
  const { sessionCode } = useParams<{ sessionCode: string }>();
  const { serverId, state, error, loading } = useServerState(sessionCode);
  const navigate = useNavigate();

  if (loading) return <p>Loading...</p>;
  if (error || !state || !serverId) {
    return (
      <div style={{ textAlign: "center", padding: "48px" }}>
        <p style={{ color: "red" }}>{error || "Session not found."}</p>
        <button onClick={() => navigate("/")}>Back</button>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: "900px", margin: "0 auto", padding: "16px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>Jacky Music</h1>
        <span style={{ opacity: 0.5 }}>Session: {sessionCode}</span>
      </div>

      <NowPlaying track={state.currentTrack} isPaused={state.isPaused} />
      <PlaybackControls state={state} serverId={serverId} />
      <SearchPanel serverId={serverId} />
      <Queue queue={state.queue} serverId={serverId} />
      <PlaylistManager serverId={serverId} currentQueue={state.queue} currentTrack={state.currentTrack} />
      <HistoryPanel serverId={serverId} />
    </div>
  );
}
