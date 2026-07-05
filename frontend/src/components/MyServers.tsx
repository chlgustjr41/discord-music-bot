import { useEffect, useRef, useState } from "react";
import {
  collection,
  doc,
  getDocs,
  onSnapshot,
  query,
  updateDoc,
  where,
} from "firebase/firestore";
import { useNavigate } from "react-router-dom";
import { db } from "../firebase";
import { useAuth } from "../hooks/useAuth";
import type { KnownVoiceChannel, ServerState } from "../types";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  ExternalLink,
  Loader2,
  LogIn,
  LogOut,
  Music,
  Plus,
  Volume2,
} from "lucide-react";

interface LiveServer {
  id: string;
  state: Partial<ServerState> | null;
}

/**
 * FUTURE #2 v2 — the personal dashboard for logged-in users (/me).
 *
 * Every owned server doc is LIVE-subscribed, so cards always reflect
 * reality: a running session shows a join button; an idle server shows
 * summon buttons for previously-visited voice channels. Summons resolve
 * through the same live subscription (no one-shot reads, no stale UI —
 * the failure mode of the first SummonPanel). Anonymous visitors keep
 * using the session-code entry at /app.
 */
export function MyServers() {
  const { user, loading: authLoading, error: authError, signInWithGoogle, logout } = useAuth();
  const [servers, setServers] = useState<LiveServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState<string | null>(null); // "serverId:channelId"
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const pendingRef = useRef<string | null>(null);

  // Discover owned servers once per login, then live-subscribe each doc.
  useEffect(() => {
    if (!user) {
      setServers([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    const unsubs: (() => void)[] = [];
    let cancelled = false;
    (async () => {
      try {
        const owned = await getDocs(
          query(collection(db, "serverOwners"), where("firebaseUid", "==", user.uid))
        );
        const ids = owned.docs
          .filter((d) => d.data().isActive !== false)
          .map((d) => d.id);
        if (cancelled) return;
        setServers(ids.map((id) => ({ id, state: null })));
        for (const id of ids) {
          unsubs.push(
            onSnapshot(doc(db, "servers", id), (snap) => {
              const state = (snap.data() as Partial<ServerState>) ?? null;
              setServers((prev) =>
                prev.map((s) => (s.id === id ? { ...s, state } : s))
              );
              // A pending summon on this server resolved into a session code.
              if (pendingRef.current?.startsWith(`${id}:`)) {
                const channelId = pendingRef.current.split(":")[1];
                if (state?.sessionCode && state?.voiceChannelId === channelId) {
                  pendingRef.current = null;
                  navigate(`/dashboard/${state.sessionCode}`);
                } else if (state && state.summonRequest == null && !state.sessionCode) {
                  pendingRef.current = null;
                  setPending(null);
                  setError("The bot couldn't join that channel — is it still there?");
                }
              }
            })
          );
        }
      } catch {
        if (!cancelled) setError("Could not load your servers.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      unsubs.forEach((u) => u());
    };
  }, [user, navigate]);

  const summon = async (serverId: string, channel: KnownVoiceChannel) => {
    const key = `${serverId}:${channel.id}`;
    setPending(key);
    pendingRef.current = key;
    setError("");
    try {
      await updateDoc(doc(db, "servers", serverId), {
        summonRequest: { channelId: channel.id },
      });
    } catch {
      setPending(null);
      pendingRef.current = null;
      setError("Failed to send the summon request.");
      return;
    }
    // The live subscription resolves success/refusal; this only breaks a hang.
    setTimeout(() => {
      if (pendingRef.current === key) {
        pendingRef.current = null;
        setPending(null);
        setError("The bot didn't respond — is it online?");
      }
    }, 20000);
  };

  if (authLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  if (!user) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <Card className="w-full max-w-md">
          <CardHeader className="text-center">
            <CardTitle className="text-2xl font-bold">My Servers</CardTitle>
            <CardDescription>
              Sign in to see your servers, summon the bot, and jump into live
              sessions. No account? Enter a session code instead.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Button onClick={signInWithGoogle} className="w-full" size="lg">
              <LogIn className="mr-2 h-4 w-4" />
              Sign in with Google
            </Button>
            {authError && (
              <p className="text-center text-sm text-destructive">{authError}</p>
            )}
            <Button variant="ghost" className="w-full" onClick={() => navigate("/app")}>
              Use a session code instead
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <>
      <header className="border-b bg-card/50 backdrop-blur supports-backdrop-filter:bg-card/60">
        <div className="mx-auto flex max-w-3xl items-center px-4 py-3">
          <button
            type="button"
            onClick={() => navigate("/")}
            className="-mx-2 flex items-center gap-2 rounded-md px-2 py-1 text-sm font-semibold transition-colors hover:bg-muted/50"
          >
            <img src="/favicon.svg" alt="" className="h-6 w-6" />
            <span>Jacky Music</span>
          </button>
          <div className="ml-auto flex items-center gap-2">
            <span className="hidden text-xs text-muted-foreground sm:inline">{user.email}</span>
            <Button
              variant="ghost"
              size="sm"
              className="text-muted-foreground hover:text-foreground"
              onClick={logout}
            >
              <LogOut className="mr-1 h-3 w-3" />
              Sign out
            </Button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-3xl space-y-4 p-4">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold">My Servers</h1>
          <Button size="sm" variant="outline" onClick={() => navigate("/activate")}>
            <Plus className="mr-1 h-3 w-3" />
            Activate a server
          </Button>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}

        {loading ? (
          <div className="flex items-center justify-center py-10 text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading your servers…
          </div>
        ) : servers.length === 0 ? (
          <Card>
            <CardContent className="space-y-3 p-6 text-center">
              <p className="text-sm text-muted-foreground">
                No servers are activated for this account yet.
              </p>
              <Button onClick={() => navigate("/activate")}>
                <Plus className="mr-2 h-4 w-4" />
                Activate your first server
              </Button>
            </CardContent>
          </Card>
        ) : (
          servers.map(({ id, state }) => {
            const live = !!state?.sessionCode;
            const channels = state?.knownVoiceChannels ?? [];
            return (
              <Card key={id}>
                <CardContent className="space-y-3 p-4">
                  <div className="flex items-center gap-3">
                    {state?.serverIcon ? (
                      <img src={state.serverIcon} alt="" className="h-10 w-10 rounded-full" />
                    ) : (
                      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10">
                        <Music className="h-5 w-5 text-primary" />
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">{state?.serverName || id}</p>
                      <div className="mt-0.5 flex items-center gap-2">
                        <div
                          className={`h-2 w-2 rounded-full ${live ? "bg-green-500" : "bg-muted-foreground/40"}`}
                        />
                        <span className="text-xs text-muted-foreground">
                          {live
                            ? `Live in ${state?.voiceChannelName ?? "voice"}`
                            : "No active session"}
                        </span>
                        {live && (
                          <Badge variant="secondary" className="font-mono text-[10px]">
                            {state?.sessionCode}
                          </Badge>
                        )}
                      </div>
                    </div>
                    {live && (
                      <Button size="sm" onClick={() => navigate(`/dashboard/${state!.sessionCode}`)}>
                        <ExternalLink className="mr-1 h-3 w-3" />
                        Join
                      </Button>
                    )}
                  </div>

                  {!live &&
                    (channels.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {channels.map((channel) => {
                          const key = `${id}:${channel.id}`;
                          return (
                            <Button
                              key={channel.id}
                              size="sm"
                              variant="outline"
                              disabled={pending !== null}
                              onClick={() => summon(id, channel)}
                            >
                              {pending === key ? (
                                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                              ) : (
                                <Volume2 className="mr-1 h-3 w-3" />
                              )}
                              {channel.name}
                            </Button>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="text-xs text-muted-foreground">
                        No visited voice channels yet — run{" "}
                        <span className="font-mono">j!start</span> once and they'll appear here.
                      </p>
                    ))}
                </CardContent>
              </Card>
            );
          })
        )}
      </div>
    </>
  );
}
