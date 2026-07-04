import { useEffect, useRef, useState } from "react";
import {
  collection,
  doc,
  getDoc,
  getDocs,
  onSnapshot,
  query,
  updateDoc,
  where,
} from "firebase/firestore";
import { useNavigate } from "react-router-dom";
import { db } from "../firebase";
import type { KnownVoiceChannel } from "../types";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ExternalLink, Loader2, Volume2 } from "lucide-react";

interface OwnedServer {
  id: string;
  name: string;
  icon?: string;
  sessionCode: string | null;
  channels: KnownVoiceChannel[];
}

interface Props {
  firebaseUid: string;
}

/**
 * FUTURE #2 — "summon": logged-in owners see their activated servers and
 * the voice channels the bot has visited before; one click writes a
 * summonRequest that the bot's watcher picks up, then we follow the doc
 * until the fresh sessionCode appears and jump straight into the dashboard.
 */
export function SummonPanel({ firebaseUid }: Props) {
  const [servers, setServers] = useState<OwnedServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [summoning, setSummoning] = useState<string | null>(null); // channel id
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const unsubRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const owned = await getDocs(
          query(collection(db, "serverOwners"), where("firebaseUid", "==", firebaseUid))
        );
        const loaded: OwnedServer[] = [];
        for (const ownerDoc of owned.docs) {
          if (ownerDoc.data().isActive === false) continue;
          const snap = await getDoc(doc(db, "servers", ownerDoc.id));
          const data = snap.exists() ? snap.data() : {};
          loaded.push({
            id: ownerDoc.id,
            name: data.serverName || ownerDoc.id,
            icon: data.serverIcon,
            sessionCode: data.sessionCode ?? null,
            channels: data.knownVoiceChannels ?? [],
          });
        }
        if (!cancelled) setServers(loaded);
      } catch {
        if (!cancelled) setError("Could not load your servers.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      unsubRef.current?.();
    };
  }, [firebaseUid]);

  const summon = async (serverId: string, channel: KnownVoiceChannel) => {
    setSummoning(channel.id);
    setError("");
    try {
      await updateDoc(doc(db, "servers", serverId), {
        summonRequest: { channelId: channel.id },
      });
    } catch {
      setError("Failed to send the summon request.");
      setSummoning(null);
      return;
    }
    // Follow the doc: the bot joins, mints a session code, clears the request.
    const timeout = setTimeout(() => {
      unsubRef.current?.();
      setSummoning(null);
      setError("The bot didn't respond — is it online and the channel still there?");
    }, 20000);
    unsubRef.current = onSnapshot(doc(db, "servers", serverId), (snap) => {
      const data = snap.data();
      if (!data) return;
      if (data.sessionCode && data.voiceChannelId === channel.id) {
        clearTimeout(timeout);
        unsubRef.current?.();
        navigate(`/dashboard/${data.sessionCode}`);
      } else if (data.summonRequest === null && !data.sessionCode) {
        // Bot consumed the request but refused (channel gone / already busy).
        clearTimeout(timeout);
        unsubRef.current?.();
        setSummoning(null);
        setError("The bot couldn't join that channel.");
      }
    });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-6 text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading your servers…
      </div>
    );
  }
  if (servers.length === 0) return null;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-lg">Your Servers</CardTitle>
        <CardDescription>
          Summon the bot into a voice channel it has visited before, or jump
          into a session that's already live.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {servers.map((server) => (
          <div key={server.id} className="space-y-2">
            <div className="flex items-center gap-2">
              {server.icon && (
                <img src={server.icon} alt="" className="h-6 w-6 rounded-full" />
              )}
              <p className="text-sm font-medium truncate">{server.name}</p>
            </div>
            {server.sessionCode ? (
              <Button
                size="sm"
                className="w-full"
                onClick={() => navigate(`/dashboard/${server.sessionCode}`)}
              >
                <ExternalLink className="mr-2 h-3 w-3" />
                Open live session
              </Button>
            ) : server.channels.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {server.channels.map((channel) => (
                  <Button
                    key={channel.id}
                    size="sm"
                    variant="outline"
                    disabled={summoning !== null}
                    onClick={() => summon(server.id, channel)}
                  >
                    {summoning === channel.id ? (
                      <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                    ) : (
                      <Volume2 className="mr-1 h-3 w-3" />
                    )}
                    {channel.name}
                  </Button>
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                No visited voice channels yet — start one session with{" "}
                <span className="font-mono">j!start</span> and it will appear here.
              </p>
            )}
          </div>
        ))}
        {error && <p className="text-sm text-destructive">{error}</p>}
      </CardContent>
    </Card>
  );
}
