import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { doc, getDoc } from "firebase/firestore";
import { db } from "../firebase";
import { useAuth } from "../hooks/useAuth";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import {
  Music,
  ArrowRight,
  Shield,
  LogIn,
  LogOut,
  Bot,
  ExternalLink,
  User,
} from "lucide-react";

const BOT_CLIENT_ID = import.meta.env.VITE_DISCORD_CLIENT_ID || "";

export function EntryScreen() {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { user, loading: authLoading, signInWithGoogle, logout } = useAuth();
  const navigate = useNavigate();

  const handleConnect = async () => {
    if (!code.trim()) return;
    setLoading(true);
    setError("");

    const codeDoc = await getDoc(doc(db, "sessionCodes", code.toUpperCase()));
    if (!codeDoc.exists()) {
      setError("Invalid or expired session code.");
      setLoading(false);
      return;
    }

    navigate(`/dashboard/${code.toUpperCase()}`);
  };

  const botInviteUrl = BOT_CLIENT_ID
    ? `https://discord.com/oauth2/authorize?client_id=${BOT_CLIENT_ID}&permissions=3147776&scope=bot`
    : "";

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className="mx-auto mb-2 flex h-16 w-16 items-center justify-center rounded-full bg-primary/10">
            <Music className="h-8 w-8 text-primary" />
          </div>
          <CardTitle className="text-3xl font-bold">Jacky Music</CardTitle>
          <CardDescription>
            Enter your session code to access the playlist
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex gap-2">
            <Input
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              placeholder="Enter session code"
              maxLength={6}
              className="text-center text-xl tracking-[0.25em] font-mono"
              onKeyDown={(e) => e.key === "Enter" && handleConnect()}
            />
            <Button
              onClick={handleConnect}
              disabled={loading || !code.trim()}
            >
              {loading ? "..." : <ArrowRight className="h-4 w-4" />}
            </Button>
          </div>

          {error && (
            <p className="text-sm text-destructive text-center">{error}</p>
          )}

          <Separator />

          {/* Account section */}
          {!authLoading && user ? (
            <div className="space-y-3">
              <div className="flex items-center gap-3 rounded-lg border p-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary/10">
                  {user.photoURL ? (
                    <img
                      src={user.photoURL}
                      alt=""
                      className="h-9 w-9 rounded-full object-cover"
                    />
                  ) : (
                    <User className="h-4 w-4 text-primary" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="truncate text-sm font-medium">
                    {user.displayName || "User"}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {user.email}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="shrink-0 text-muted-foreground hover:text-foreground"
                  onClick={logout}
                >
                  <LogOut className="mr-1 h-3 w-3" />
                  Sign Out
                </Button>
              </div>

              <div className="flex gap-2">
                <Button
                  variant="outline"
                  className="flex-1"
                  onClick={() => navigate("/activate")}
                >
                  <Shield className="mr-2 h-4 w-4" />
                  Activate Server
                </Button>
                {botInviteUrl && (
                  <Button
                    variant="outline"
                    className="flex-1"
                    onClick={() => window.open(botInviteUrl, "_blank")}
                  >
                    <Bot className="mr-2 h-4 w-4" />
                    Add Bot
                    <ExternalLink className="ml-1 h-3 w-3" />
                  </Button>
                )}
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <Button
                variant="outline"
                className="w-full"
                onClick={signInWithGoogle}
                disabled={authLoading}
              >
                <LogIn className="mr-2 h-4 w-4" />
                Sign in with Google
              </Button>
              <p className="text-xs text-center text-muted-foreground">
                Sign in to activate servers or add the bot to new servers
              </p>
            </div>
          )}

          {/* Setup guide */}
          {!authLoading && user && (
            <>
              <Separator />
              <div className="space-y-2">
                <p className="text-sm font-medium text-center">
                  Setting up a new server?
                </p>
                <ol className="space-y-1.5 text-xs text-muted-foreground list-decimal list-inside">
                  <li>
                    Click <span className="font-medium text-foreground">Add Bot</span> above
                    to invite Jacky Music to your Discord server
                  </li>
                  <li>
                    Click <span className="font-medium text-foreground">Activate Server</span> and
                    enter your Discord Server ID to enable the bot
                  </li>
                  <li>
                    In Discord, type{" "}
                    <code className="rounded bg-muted px-1">j!play &lt;song&gt;</code>{" "}
                    to start a session
                  </li>
                  <li>
                    Enter the session code above to control playback from the web
                  </li>
                </ol>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
