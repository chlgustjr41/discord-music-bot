import { useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useServerState } from "../hooks/useServerState";
import { useActivityToasts } from "../hooks/useActivityToasts.js";
import { NowPlaying } from "./NowPlaying";
import { Queue } from "./Queue";
import { PlaybackControls } from "./PlaybackControls";
import { SearchPanel } from "./SearchPanel";
import { PlaylistManager } from "./PlaylistManager";
import { CommandHistory } from "./CommandHistory";
import { MusicHistory } from "./MusicHistory";
import { ActivityLog } from "./ActivityLog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Music, Loader2, WifiOff, AlertTriangle, LogOut } from "lucide-react";

export function Dashboard() {
  const { sessionCode } = useParams<{ sessionCode: string }>();
  const { serverId, state, error, loading, sessionExpired } = useServerState(sessionCode);
  const navigate = useNavigate();
  const logEntries = useActivityToasts(state);

  const botConnected = !!state?.voiceChannelId;

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
    <div className="mx-auto max-w-3xl p-4 space-y-4">
      {/* Header with server info */}
      <Card>
        <CardContent className="flex items-center gap-4 p-4">
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
            </div>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="shrink-0 text-muted-foreground hover:text-foreground"
            onClick={() => navigate("/")}
          >
            <LogOut className="mr-1 h-4 w-4" />
            Exit
          </Button>
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
      <SearchPanel serverId={serverId} searchResults={state.searchResults} searchQuery={state.searchQuery} />
      <PlaylistManager serverId={serverId} currentQueue={state.queue} currentTrack={state.currentTrack} searchResults={state.searchResults} searchQuery={state.searchQuery} />
      <CommandHistory serverId={serverId} />
      <MusicHistory serverId={serverId} />
      <ActivityLog entries={logEntries} />
    </div>
  );
}
