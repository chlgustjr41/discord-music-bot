import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { doc, updateDoc } from "firebase/firestore";
import { db } from "../firebase";
import { useServerState } from "../hooks/useServerState";
import { useActivityToasts } from "../hooks/useActivityToasts.js";
import { useAuth } from "../hooks/useAuth";
import { usePresence } from "../hooks/usePresence";
import { NowPlaying } from "./NowPlaying";
import { Queue } from "./Queue";
import { PlaybackControls } from "./PlaybackControls";
import { SearchPanel } from "./SearchPanel";
import { PlaylistManager } from "./PlaylistManager";
import { CommandHistory } from "./CommandHistory";
import { MusicHistory } from "./MusicHistory";
import { StatsPanel } from "./StatsPanel";
import { IdentityChip } from "./IdentityChip";
import { PinServerButton } from "./PinServerButton";
import { AccountMenu } from "./AccountMenu";
import { PresenceBar } from "./PresenceBar";
import { ActivityLog } from "./ActivityLog";
import { NodeStatus } from "./NodeStatus";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Music, Loader2, WifiOff, AlertTriangle, LogOut, RotateCcw } from "lucide-react";

/**
 * The session code is this screen's IDENTITY, not merely an input to it.
 *
 * React Router reuses one component instance when only the param changes, so
 * every per-session initializer inside DashboardView would otherwise run once
 * per mount and carry the previous session's state into the next one. Keying
 * on the code makes the whole screen re-initialise instead.
 */
export function Dashboard() {
  const { sessionCode } = useParams<{ sessionCode: string }>();
  return <DashboardView key={sessionCode} sessionCode={sessionCode} />;
}

function DashboardView({ sessionCode }: { sessionCode: string | undefined }) {
  const { serverId, state, error, loading, sessionExpired } = useServerState(sessionCode);
  const navigate = useNavigate();
  const logEntries = useActivityToasts(state);
  const { user } = useAuth();
  const [exiting, setExiting] = useState(false);
  const [resetting, setResetting] = useState(false);
  // Presence changes on join, leave and heartbeat only — a handful of events
  // per session — so it lives here with everything else rather than behind an
  // isolating wrapper.
  const { participants } = usePresence(sessionCode, user);

  const botConnected = !!state?.voiceChannelId;

  // Force-rebuild the voice session (buggy-scenario escape hatch): the bot
  // disconnects, rejoins, and resumes — queue and this web session survive.
  async function handleReset() {
    if (resetting || !serverId) return;
    setResetting(true);
    try {
      await updateDoc(doc(db, "servers", serverId), { resetRequested: true });
    } catch {
      setResetting(false);
      return;
    }
    setTimeout(() => setResetting(false), 12000);
  }

  useEffect(() => {
    // The bot clears the flag when the reset completes.
    if (resetting && state && !state.resetRequested) setResetting(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state?.resetRequested]);

  async function handleExit() {
    if (exiting) return;
    setExiting(true);
    if (!serverId) {
      navigate("/");
      return;
    }
    try {
      await updateDoc(doc(db, "servers", serverId), {
        endSessionRequested: true,
      });
    } catch {
      // If we can't even reach Firestore, just bail out to landing.
      navigate("/");
      return;
    }
    // Safety: if the bot never picks this up (offline), navigate manually.
    setTimeout(() => navigate("/"), 5000);
  }

  useEffect(() => {
    if (sessionExpired) {
      const timeout = setTimeout(() => navigate("/", { replace: true }), 2000);
      return () => clearTimeout(timeout);
    }
  }, [sessionExpired, navigate]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  if (sessionExpired) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4">
        <AlertTriangle className="h-10 w-10 text-yellow-500" />
        <p className="text-lg font-medium">Session expired</p>
        <p className="text-sm text-muted-foreground">
          A new session was started or this session has ended. Redirecting...
        </p>
      </div>
    );
  }

  if (error || !state || !serverId) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4">
        <p className="text-destructive">{error || "Session not found."}</p>
        <Button variant="outline" onClick={() => navigate("/")}>
          Back
        </Button>
      </div>
    );
  }

  return (
    <>
      <header className="border-b bg-card/50 backdrop-blur supports-backdrop-filter:bg-card/60">
        <div className="mx-auto max-w-3xl flex items-center px-4 py-3">
          <button
            type="button"
            onClick={() => navigate("/")}
            className="flex items-center gap-2 rounded-md px-2 py-1 -mx-2 text-sm font-semibold hover:bg-muted/50 transition-colors"
            aria-label="Back to landing page"
          >
            <img src="/favicon.svg" alt="" className="h-6 w-6" />
            <span>Jacky Music</span>
          </button>
        </div>
      </header>
      <div className="mx-auto max-w-3xl p-4 space-y-4">
        {/* Header with server info */}
        <Card>
        <CardContent className="flex flex-wrap items-center gap-4 p-4">
          {state.serverIcon ? (
            <img
              src={state.serverIcon}
              alt=""
              className="h-12 w-12 rounded-full object-cover"
            />
          ) : (
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
              <Music className="h-6 w-6 text-primary" />
            </div>
          )}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-xl font-bold">
                {state.serverName || "Jacky Music"}
              </h1>
              <div className={`h-2 w-2 shrink-0 rounded-full ${botConnected ? "bg-green-500" : "bg-red-500"}`} />
            </div>
            <div className="flex items-center gap-2 mt-0.5 flex-wrap">
              {botConnected && state.voiceChannelName && (
                <>
                  <span className="text-xs text-muted-foreground">{state.voiceChannelName}</span>
                  <span className="text-xs text-muted-foreground/40">·</span>
                </>
              )}
              <span className="text-xs text-muted-foreground">Session</span>
              <Badge variant="secondary" className="font-mono text-xs">
                {sessionCode}
              </Badge>
              <span className="text-xs text-muted-foreground/40">·</span>
              <NodeStatus serverId={serverId} />
            </div>
          </div>
          {/* One wrapping cluster: at narrow widths the whole group drops to
              its own line instead of squeezing (or clipping) the controls. */}
          <div className="flex w-full flex-wrap items-center justify-end gap-1 sm:w-auto sm:gap-2">
          <PresenceBar participants={participants} />
          <IdentityChip />
          <PinServerButton serverId={serverId} serverName={state.serverName} />
          <Button
            variant="ghost"
            size="sm"
            className="shrink-0 text-muted-foreground hover:text-foreground"
            onClick={handleReset}
            disabled={resetting || !botConnected}
            title="Force-restart the voice session (queue is preserved)"
          >
            {resetting ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <RotateCcw className="mr-1 h-4 w-4" />
            )}
            {resetting ? "Resetting…" : "Reset"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="shrink-0 text-muted-foreground hover:text-foreground"
            onClick={handleExit}
            disabled={exiting}
            title="Disconnect the bot and end this session"
          >
            {exiting ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <LogOut className="mr-1 h-4 w-4" />
            )}
            {exiting ? "Disconnecting…" : "Exit"}
          </Button>
          <AccountMenu />
          </div>
        </CardContent>
      </Card>

      {/* Disconnected warning banner */}
      {!botConnected && (
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="flex items-center gap-3 p-4">
            <WifiOff className="h-5 w-5 shrink-0 text-destructive" />
            <div>
              <p className="text-sm font-medium text-destructive">
                Bot disconnected
              </p>
              <p className="text-xs text-muted-foreground">
                The bot has left the voice channel. Use{" "}
                <code className="rounded bg-muted px-1">j!play</code> in
                Discord to start a new session. Controls are disabled until the
                bot reconnects.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      <NowPlaying track={state.currentTrack} isPaused={state.isPaused || !botConnected} serverId={serverId} />
      <PlaybackControls state={state} serverId={serverId} disabled={!botConnected} />
      <Queue queue={state.queue} serverId={serverId} />
      <SearchPanel serverId={serverId} searchResults={state.searchResults} searchQuery={state.searchQuery} searchPlaylistName={state.searchPlaylistName} />
      <PlaylistManager
        serverId={serverId}
        currentQueue={state.queue}
        currentTrack={state.currentTrack}
        searchResults={state.searchResults}
        searchQuery={state.searchQuery}
        searchPlaylistName={state.searchPlaylistName}
      />
      <StatsPanel serverId={serverId} />
      <MusicHistory serverId={serverId} />
      <CommandHistory serverId={serverId} />
      <ActivityLog entries={logEntries} />
      </div>
    </>
  );
}
