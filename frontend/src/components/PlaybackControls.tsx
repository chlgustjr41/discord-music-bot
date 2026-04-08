import { useState, useRef } from "react";
import { doc, updateDoc } from "firebase/firestore";
import { db } from "../firebase";
import type { ServerState } from "../types";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Card, CardContent } from "@/components/ui/card";
import { Play, Pause, SkipForward, Shuffle, Repeat, Repeat1, Volume2, MessageSquare, MessageSquareOff } from "lucide-react";

interface Props {
  state: ServerState;
  serverId: string;
  disabled?: boolean;
}

export function PlaybackControls({ state, serverId, disabled }: Props) {
  const ref = doc(db, "servers", serverId);
  const [localVolume, setLocalVolume] = useState(state.volume);
  const volumeSynced = useRef(true);

  // Keep local volume in sync with server when not actively sliding
  if (volumeSynced.current && localVolume !== state.volume) {
    setLocalVolume(state.volume);
  }

  const togglePause = () => {
    if (!state.currentTrack && state.queue.length > 0) {
      updateDoc(ref, { isPaused: true }).then(() =>
        updateDoc(ref, { isPaused: false })
      );
      return;
    }
    updateDoc(ref, { isPaused: !state.isPaused });
  };
  const skip = () => updateDoc(ref, { currentTrack: null, isPlaying: true });
  const shuffle = () => {
    const shuffled = [...state.queue].sort(() => Math.random() - 0.5);
    updateDoc(ref, { queue: shuffled });
  };
  const cycleLoop = () => {
    const cycle: Record<string, string> = { off: "track", track: "queue", queue: "off" };
    updateDoc(ref, { loopMode: cycle[state.loopMode] });
  };

  const onVolumeChange = (val: number | readonly number[]) => {
    volumeSynced.current = false;
    const v = Array.isArray(val) ? val[0] : val;
    setLocalVolume(v);
  };

  const onVolumeCommitted = (val: number | readonly number[]) => {
    volumeSynced.current = true;
    const v = Array.isArray(val) ? val[0] : val;
    updateDoc(ref, { volume: v });
  };

  const LoopIcon = state.loopMode === "track" ? Repeat1 : Repeat;

  return (
    <Card>
      <CardContent className="flex flex-wrap items-center gap-2 p-3">
        <Button
          variant={state.isPaused || !state.currentTrack ? "default" : "secondary"}
          size="icon"
          onClick={togglePause}
          disabled={disabled}
        >
          {state.isPaused || !state.currentTrack ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
        </Button>

        <Button
          variant="secondary"
          size="icon"
          onClick={skip}
          disabled={disabled || !state.isPlaying}
        >
          <SkipForward className="h-4 w-4" />
        </Button>

        <Button
          variant="secondary"
          size="icon"
          onClick={shuffle}
          disabled={disabled || state.queue.length < 2}
        >
          <Shuffle className="h-4 w-4" />
        </Button>

        <Button
          variant={state.loopMode !== "off" ? "default" : "secondary"}
          size="icon"
          onClick={cycleLoop}
          disabled={disabled}
          title={`Loop: ${state.loopMode}`}
        >
          <LoopIcon className="h-4 w-4" />
        </Button>

        <Button
          variant={state.discordNotify !== false ? "default" : "secondary"}
          size="icon"
          onClick={() => updateDoc(ref, { discordNotify: state.discordNotify === false })}
          disabled={disabled}
          title={state.discordNotify !== false ? "Discord notifications: on" : "Discord notifications: off"}
        >
          {state.discordNotify !== false ? <MessageSquare className="h-4 w-4" /> : <MessageSquareOff className="h-4 w-4" />}
        </Button>

        <div className="ml-auto flex items-center gap-3 flex-1 max-w-xs min-w-[10rem]">
          <Volume2 className="h-4 w-4 shrink-0 text-muted-foreground" />
          <Slider
            value={[localVolume]}
            onValueChange={onVolumeChange}
            onValueCommitted={onVolumeCommitted}
            max={100}
            step={1}
            className="flex-1"
          />
          <span className="w-8 shrink-0 text-right text-xs text-muted-foreground">{localVolume}%</span>
        </div>
      </CardContent>
    </Card>
  );
}
