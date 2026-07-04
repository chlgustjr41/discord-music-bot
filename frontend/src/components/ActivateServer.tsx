import { useState } from "react";
import { doc, setDoc, serverTimestamp } from "firebase/firestore";
import { db } from "../firebase";
import { useAuth } from "../hooks/useAuth";
import { useNavigate } from "react-router-dom";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { CheckCircle, LogIn, LogOut, ArrowLeft, Loader2 } from "lucide-react";
import { SummonPanel } from "./SummonPanel";

export function ActivateServer() {
  const {
    user,
    loading: authLoading,
    error: authError,
    signInWithGoogle,
    logout,
  } = useAuth();
  const [serverId, setServerId] = useState("");
  const [status, setStatus] = useState<"idle" | "saving" | "done" | "error">(
    "idle"
  );
  const [errorMsg, setErrorMsg] = useState("");
  const navigate = useNavigate();

  const handleActivate = async () => {
    if (!serverId.trim() || !user) return;
    setStatus("saving");
    setErrorMsg("");

    try {
      await setDoc(doc(db, "serverOwners", serverId.trim()), {
        ownerDiscordId: "",
        ownerEmail: user.email,
        firebaseUid: user.uid,
        activatedAt: serverTimestamp(),
        isActive: true,
      });
      setStatus("done");
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to activate server.");
      setStatus("error");
    }
  };

  if (authLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-md space-y-4">
      {user && <SummonPanel firebaseUid={user.uid} />}
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl font-bold">
            Activate Your Server
          </CardTitle>
          <CardDescription>
            {!user
              ? "Sign in with Google to activate Jacky Music for your Discord server."
              : status === "done"
                ? "Your server is now activated!"
                : "Enter your Discord Server ID to activate."}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {!user ? (
            <>
              <Button onClick={signInWithGoogle} className="w-full" size="lg">
                <LogIn className="mr-2 h-4 w-4" />
                Sign in with Google
              </Button>
              {authError && (
                <p className="text-sm text-destructive text-center">
                  {authError}
                </p>
              )}
            </>
          ) : status === "done" ? (
            <div className="space-y-4 text-center">
              <CheckCircle className="mx-auto h-12 w-12 text-primary" />
              <p className="text-foreground">
                Server{" "}
                <span className="font-mono font-bold">{serverId}</span> has
                been activated!
              </p>
              <p className="text-sm text-muted-foreground">
                Jacky Music will now respond to commands in your server.
              </p>
              <Button
                onClick={() => navigate("/")}
                variant="outline"
                className="w-full"
              >
                <ArrowLeft className="mr-2 h-4 w-4" />
                Back to Home
              </Button>
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">
                  Signed in as{" "}
                  <span className="font-medium text-foreground">
                    {user.email}
                  </span>
                </p>
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-muted-foreground hover:text-foreground"
                  onClick={logout}
                >
                  <LogOut className="mr-1 h-3 w-3" />
                  Sign Out
                </Button>
              </div>
              <div className="space-y-2">
                <Input
                  type="text"
                  value={serverId}
                  onChange={(e) => setServerId(e.target.value)}
                  placeholder="Discord Server ID"
                  className="font-mono"
                />
                <p className="text-xs text-muted-foreground">
                  Right-click your server name in Discord → Copy Server ID.
                  Enable Developer Mode in Discord settings if you don't see
                  this option.
                </p>
              </div>
              <Button
                onClick={handleActivate}
                disabled={status === "saving" || !serverId.trim()}
                className="w-full"
              >
                {status === "saving" ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Activating...
                  </>
                ) : (
                  "Activate Server"
                )}
              </Button>
              {errorMsg && (
                <p className="text-sm text-destructive text-center">
                  {errorMsg}
                </p>
              )}
            </>
          )}

          <Button
            variant="ghost"
            size="sm"
            className="w-full text-muted-foreground"
            onClick={() => navigate("/")}
          >
            <ArrowLeft className="mr-2 h-4 w-4" />
            Back
          </Button>
        </CardContent>
      </Card>
      </div>
    </div>
  );
}
