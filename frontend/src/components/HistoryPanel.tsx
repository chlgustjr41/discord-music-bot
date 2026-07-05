import { useState, useEffect } from "react";
import { collection, getDocs, query, orderBy, limit, doc, updateDoc, arrayUnion } from "firebase/firestore";
import { db } from "../firebase";
import { getIdentityName } from "../lib/identity";
import type { HistorySession } from "../types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { History, ChevronDown, RotateCcw, Clock } from "lucide-react";

interface Props {
  serverId: string;
}

export function HistoryPanel({ serverId }: Props) {
  const [sessions, setSessions] = useState<HistorySession[]>([]);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!expanded) return;
    const q = query(
      collection(db, "servers", serverId, "history"),
      orderBy("startedAt", "desc"),
      limit(5)
    );
    getDocs(q).then((snap) => {
      setSessions(snap.docs.map((d) => ({ id: d.id, ...d.data() } as HistorySession)));
    });
  }, [expanded, serverId]);

  const requeueSession = async (session: HistorySession) => {
    for (const track of session.tracks) {
      await updateDoc(doc(db, "servers", serverId), {
        queue: arrayUnion({
          title: track.title,
          artist: track.artist,
          url: track.url,
          thumbnail: track.thumbnail,
          duration: track.duration,
          requestedBy: getIdentityName(),
        }),
      });
    }
  };

  return (
    <Card>
      <Collapsible open={expanded} onOpenChange={setExpanded}>
        <CardHeader className="pb-3">
          <CollapsibleTrigger className="flex w-full items-center gap-2">
            <CardTitle className="flex items-center gap-2 text-lg">
              <History className="h-4 w-4" />
              History
            </CardTitle>
            <ChevronDown className={`ml-auto h-4 w-4 text-muted-foreground transition-transform ${expanded ? "rotate-180" : ""}`} />
          </CollapsibleTrigger>
        </CardHeader>
        <CollapsibleContent>
          <CardContent className="space-y-3 pt-0">
            {sessions.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-4 text-muted-foreground">
                <Clock className="mb-1 h-6 w-6" />
                <p className="text-sm">No history yet.</p>
              </div>
            ) : (
              <div className="space-y-3">
                {sessions.map((s) => (
                  <div key={s.id} className="rounded-lg border p-3 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">
                        {s.startedAt?.slice(0, 10) || "Unknown date"}
                      </span>
                      <Button size="sm" variant="ghost" onClick={() => requeueSession(s)}>
                        <RotateCcw className="mr-1 h-3 w-3" />
                        Re-queue
                      </Button>
                    </div>
                    <ul className="space-y-0.5 pl-2">
                      {s.tracks.slice(0, 5).map((t, i) => (
                        <li key={i} className="text-xs text-muted-foreground">
                          {t.title} — {t.artist}
                        </li>
                      ))}
                      {s.tracks.length > 5 && (
                        <li className="text-xs text-muted-foreground/60">
                          ...and {s.tracks.length - 5} more
                        </li>
                      )}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </CollapsibleContent>
      </Collapsible>
    </Card>
  );
}
