import { useEffect, useMemo, useState } from "react";
import { collection, limit, onSnapshot, orderBy, query } from "firebase/firestore";
import { db } from "../firebase";
import { addTracksToQueue } from "../lib/social";
import type { MusicHistoryEntry, Track } from "../types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  ChevronDown,
  Crown,
  GripVertical,
  Mic2,
  Plus,
  Trophy,
  UserRound,
  Users,
} from "lucide-react";

interface DragStat {
  id: string;
  title: string;
  artist: string;
  thumbnail?: string;
  url?: string;
  count: number;
}

interface MemberStat {
  id: string;
  name: string;
  queueAdds?: number;
  drags?: number;
  searches?: number;
  total?: number;
}

interface Props {
  serverId: string;
}

type Tab = "tracks" | "artists" | "requesters" | "dragged" | "members";

const TABS: { id: Tab; label: string; icon: typeof Trophy }[] = [
  { id: "tracks", label: "Top Tracks", icon: Trophy },
  { id: "artists", label: "Artists", icon: Mic2 },
  { id: "requesters", label: "Requesters", icon: Users },
  { id: "dragged", label: "Most Dragged", icon: GripVertical },
  { id: "members", label: "Members", icon: UserRound },
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
  const [members, setMembers] = useState<MemberStat[]>([]);
  const [addedMsg, setAddedMsg] = useState("");
  const [adding, setAdding] = useState<string | null>(null);

  const addToQueue = async (id: string, track: Partial<Track>) => {
    if (!track.url || adding) return;
    setAdding(id);
    try {
      await addTracksToQueue(serverId, [{
        title: track.title || "Unknown",
        artist: track.artist || "",
        url: track.url,
        thumbnail: track.thumbnail || "",
        duration: track.duration || 0,
        requestedBy: "",  // stamped by addTracksToQueue
      }]);
      setAddedMsg(`Added “${track.title}” to the queue`);
      setTimeout(() => setAddedMsg(""), 3000);
    } catch {
      setAddedMsg("Failed to add — try again");
    } finally {
      setAdding(null);
    }
  };

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
    const unsubMembers = onSnapshot(
      query(
        collection(db, "servers", serverId, "memberStats"),
        orderBy("total", "desc"),
        limit(10)
      ),
      (snap) => setMembers(snap.docs.map((d) => ({ id: d.id, ...d.data() } as MemberStat)))
    );
    return () => {
      unsubTracks();
      unsubDrags();
      unsubMembers();
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

            {addedMsg && <p className="text-xs text-primary">{addedMsg}</p>}

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
                      <button
                        onClick={() => addToQueue(t.id, t)}
                        disabled={adding !== null || !t.url}
                        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-input text-muted-foreground transition-colors hover:border-primary hover:text-primary disabled:opacity-40"
                        title="Add to queue"
                      >
                        <Plus className="h-3.5 w-3.5" />
                      </button>
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
                      <button
                        onClick={() => addToQueue(d.id, d)}
                        disabled={adding !== null || !d.url}
                        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-input text-muted-foreground transition-colors hover:border-primary hover:text-primary disabled:opacity-40"
                        title="Add to queue"
                      >
                        <Plus className="h-3.5 w-3.5" />
                      </button>
                    </li>
                  ))}
                </ul>
              ))}

            {tab === "members" &&
              (members.length === 0 ? (
                <p className="py-3 text-center text-xs text-muted-foreground">
                  Nobody has claimed a name yet — set yours with the name chip
                  up top and your adds, drags, and searches get counted.
                </p>
              ) : (
                <ul className="space-y-1">
                  {members.map((m, i) => (
                    <li
                      key={m.id}
                      className="flex items-center gap-2 rounded-md p-2 hover:bg-muted/50"
                    >
                      {rank(i)}
                      <p className="min-w-0 flex-1 truncate text-sm font-medium">{m.name}</p>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {m.queueAdds || 0} adds · {m.drags || 0} drags · {m.searches || 0} searches
                      </span>
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
