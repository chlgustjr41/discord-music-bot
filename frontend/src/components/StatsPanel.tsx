import { useEffect, useMemo, useState } from "react";
import { collection, limit, onSnapshot, orderBy, query } from "firebase/firestore";
import { db } from "../firebase";
import type { MusicHistoryEntry } from "../types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ChevronDown, Crown, GripVertical, Mic2, Trophy, Users } from "lucide-react";

interface DragStat {
  id: string;
  title: string;
  artist: string;
  thumbnail?: string;
  count: number;
}

interface Props {
  serverId: string;
}

type Tab = "tracks" | "artists" | "requesters" | "dragged";

const TABS: { id: Tab; label: string; icon: typeof Trophy }[] = [
  { id: "tracks", label: "Top Tracks", icon: Trophy },
  { id: "artists", label: "Artists", icon: Mic2 },
  { id: "requesters", label: "Requesters", icon: Users },
  { id: "dragged", label: "Most Dragged", icon: GripVertical },
];

const MEDALS = ["🥇", "🥈", "🥉"];

/**
 * FUTURE #3 — channel leaderboard. Aggregates the session channel's history:
 * most played tracks/artists (musicHistory playCount), busiest requesters,
 * and the most dragged-around queue tracks (dragStats, written by Queue).
 */
export function StatsPanel({ serverId }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [tab, setTab] = useState<Tab>("tracks");
  const [tracks, setTracks] = useState<MusicHistoryEntry[]>([]);
  const [drags, setDrags] = useState<DragStat[]>([]);

  useEffect(() => {
    if (!expanded) return;
    const unsubTracks = onSnapshot(
      query(
        collection(db, "servers", serverId, "musicHistory"),
        orderBy("playCount", "desc"),
        limit(100)
      ),
      (snap) =>
        setTracks(snap.docs.map((d) => ({ id: d.id, ...d.data() } as MusicHistoryEntry)))
    );
    const unsubDrags = onSnapshot(
      query(
        collection(db, "servers", serverId, "dragStats"),
        orderBy("count", "desc"),
        limit(10)
      ),
      (snap) => setDrags(snap.docs.map((d) => ({ id: d.id, ...d.data() } as DragStat)))
    );
    return () => {
      unsubTracks();
      unsubDrags();
    };
  }, [expanded, serverId]);

  const artists = useMemo(() => {
    const totals = new Map<string, number>();
    for (const t of tracks) {
      const artist = t.artist?.trim();
      if (!artist) continue;
      totals.set(artist, (totals.get(artist) ?? 0) + (t.playCount || 1));
    }
    return [...totals.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10);
  }, [tracks]);

  const requesters = useMemo(() => {
    const totals = new Map<string, number>();
    for (const t of tracks) {
      const who = t.requestedBy?.trim() || "Unknown";
      totals.set(who, (totals.get(who) ?? 0) + (t.playCount || 1));
    }
    return [...totals.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10);
  }, [tracks]);

  const rank = (i: number) => (
    <span className="w-6 shrink-0 text-center text-sm">
      {MEDALS[i] ?? <span className="text-xs text-muted-foreground">{i + 1}</span>}
    </span>
  );

  const nameCountRows = (rows: [string, number][], unit: string) =>
    rows.length === 0 ? (
      <p className="py-3 text-center text-xs text-muted-foreground">No data yet.</p>
    ) : (
      <ul className="space-y-1">
        {rows.map(([name, count], i) => (
          <li key={name} className="flex items-center gap-2 rounded-md p-2 hover:bg-muted/50">
            {rank(i)}
            <p className="flex-1 truncate text-sm font-medium">{name}</p>
            <Badge variant="secondary" className="text-xs">
              {count} {unit}
            </Badge>
          </li>
        ))}
      </ul>
    );

  return (
    <Card>
      <Collapsible open={expanded} onOpenChange={setExpanded}>
        <CardHeader className="pb-3">
          <CollapsibleTrigger className="flex w-full items-center gap-2">
            <CardTitle className="flex items-center gap-2 text-lg">
              <Crown className="h-4 w-4" />
              Channel Leaderboard
            </CardTitle>
            <ChevronDown
              className={`ml-auto h-4 w-4 text-muted-foreground transition-transform ${expanded ? "rotate-180" : ""}`}
            />
          </CollapsibleTrigger>
        </CardHeader>
        <CollapsibleContent>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap gap-1">
              {TABS.map(({ id, label, icon: Icon }) => (
                <button
                  key={id}
                  onClick={() => setTab(id)}
                  className={`flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                    tab === id
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted text-muted-foreground hover:text-foreground"
                  }`}
                >
                  <Icon className="h-3 w-3" />
                  {label}
                </button>
              ))}
            </div>

            {tab === "tracks" &&
              (tracks.length === 0 ? (
                <p className="py-3 text-center text-xs text-muted-foreground">
                  No plays recorded yet.
                </p>
              ) : (
                <ul className="space-y-1">
                  {tracks.slice(0, 10).map((t, i) => (
                    <li
                      key={t.id}
                      className="flex items-center gap-2 rounded-md p-2 hover:bg-muted/50"
                    >
                      {rank(i)}
                      {t.thumbnail && (
                        <img
                          src={t.thumbnail}
                          alt=""
                          loading="lazy"
                          className="h-8 w-14 shrink-0 rounded object-cover bg-muted"
                        />
                      )}
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium">{t.title}</p>
                        <p className="truncate text-xs text-muted-foreground">{t.artist}</p>
                      </div>
                      <Badge variant="secondary" className="text-xs">
                        {t.playCount || 1} plays
                      </Badge>
                    </li>
                  ))}
                </ul>
              ))}

            {tab === "artists" && nameCountRows(artists, "plays")}
            {tab === "requesters" && nameCountRows(requesters, "plays")}

            {tab === "dragged" &&
              (drags.length === 0 ? (
                <p className="py-3 text-center text-xs text-muted-foreground">
                  Nobody has fought over the queue order yet.
                </p>
              ) : (
                <ul className="space-y-1">
                  {drags.map((d, i) => (
                    <li
                      key={d.id}
                      className="flex items-center gap-2 rounded-md p-2 hover:bg-muted/50"
                    >
                      {rank(i)}
                      {d.thumbnail && (
                        <img
                          src={d.thumbnail}
                          alt=""
                          loading="lazy"
                          className="h-8 w-14 shrink-0 rounded object-cover bg-muted"
                        />
                      )}
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium">{d.title}</p>
                        <p className="truncate text-xs text-muted-foreground">{d.artist}</p>
                      </div>
                      <Badge variant="secondary" className="text-xs">
                        {d.count}× dragged
                      </Badge>
                    </li>
                  ))}
                </ul>
              ))}
          </CardContent>
        </CollapsibleContent>
      </Collapsible>
    </Card>
  );
}
