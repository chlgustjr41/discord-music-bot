import { useState, useEffect, useRef } from "react";
import { doc, updateDoc } from "firebase/firestore";
import { db } from "../firebase";
import type { CurrentTrack } from "../types";
import { Card, CardContent } from "@/components/ui/card";
import { Slider } from "@/components/ui/slider";
import { Music } from "lucide-react";

interface Props {
  track: CurrentTrack | null;
  isPaused: boolean;
  serverId: string;
}

export function NowPlaying({ track, isPaused, serverId }: Props) {
  const [elapsed, setElapsed] = useState(0);
  const [seekValue, setSeekValue] = useState<number | null>(null);
  const lastTrackUrl = useRef<string | null>(null);
  const lastStartedAt = useRef<string | null>(null);
  const elapsedRef = useRef(0);
  const isDragging = useRef(false);

  // Recalculate elapsed from startedAt whenever track or startedAt changes
  useEffect(() => {
    if (!track) {
      setElapsed(0);
      elapsedRef.current = 0;
      lastTrackUrl.current = null;
      lastStartedAt.current = null;
      return;
    }

    // Recalculate on new track OR updated startedAt (e.g. after seek)
    if (track.url !== lastTrackUrl.current || track.startedAt !== lastStartedAt.current) {
      lastTrackUrl.current = track.url;
      lastStartedAt.current = track.startedAt;
      const started = new Date(track.startedAt).getTime();
      const initial = Math.floor((Date.now() - started) / 1000);
      const clamped = Math.max(0, Math.min(initial, track.duration));
      setElapsed(clamped);
      elapsedRef.current = clamped;
    }
  }, [track]);

  // Tick elapsed every second while playing and not seeking
  useEffect(() => {
    if (!track || isPaused) return;

    const interval = setInterval(() => {
      elapsedRef.current = Math.min(elapsedRef.current + 1, track.duration);
      setElapsed(elapsedRef.current);
    }, 1000);

    return () => clearInterval(interval);
  }, [track, isPaused]);

  const onSliderPointerDown = () => {
    isDragging.current = true;
  };

  const onSeekChange = (val: number | readonly number[]) => {
    // Only respond to pointer-initiated interactions, not scroll wheel
    if (!isDragging.current) return;
    const v = Array.isArray(val) ? val[0] : val;
    setSeekValue(v);
  };

  const onSeekCommitted = (val: number | readonly number[]) => {
    // Only commit seek from pointer interactions (click/drag), not scroll wheel
    if (!isDragging.current) return;
    isDragging.current = false;

    const position = Array.isArray(val) ? val[0] : val;
    setSeekValue(null);
    elapsedRef.current = position;
    setElapsed(position);

    // Write seek request to Firestore — bot picks it up
    const ref = doc(db, "servers", serverId);
    updateDoc(ref, { seekPosition: position });
  };

  const formatTime = (s: number) => {
    const m = Math.floor(s / 60);
    return `${m}:${String(s % 60).padStart(2, "0")}`;
  };

  if (!track) {
    return (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center justify-center py-8 text-muted-foreground">
          <Music className="mb-2 h-10 w-10" />
          <p>Nothing is playing</p>
        </CardContent>
      </Card>
    );
  }

  // Show seek position while dragging, otherwise show elapsed
  const displayTime = seekValue !== null ? seekValue : elapsed;

  return (
    <Card>
      <CardContent className="flex items-center gap-4 p-4">
        {track.thumbnail ? (
          <img
            src={track.thumbnail}
            alt=""
            className="h-20 w-20 rounded-lg object-cover"
          />
        ) : (
          <div className="flex h-20 w-20 items-center justify-center rounded-lg bg-muted">
            <Music className="h-8 w-8 text-muted-foreground" />
          </div>
        )}
        <div className="flex-1 min-w-0">
          <h3 className="truncate font-semibold text-lg">{track.title}</h3>
          <p className="truncate text-sm text-muted-foreground">{track.artist}</p>
          <div className="mt-2">
            <Slider
              value={[displayTime]}
              onPointerDown={onSliderPointerDown}
              onValueChange={onSeekChange}
              onValueCommitted={onSeekCommitted}
              max={track.duration || 1}
              step={1}
              className="[&_[role=slider]]:h-3 [&_[role=slider]]:w-3"
            />
          </div>
          <div className="mt-1 flex justify-between text-xs text-muted-foreground">
            <span>{formatTime(Math.min(displayTime, track.duration))}</span>
            <span>{formatTime(track.duration)}</span>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
