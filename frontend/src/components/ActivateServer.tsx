import { useState } from "react";
import { doc, getDoc, setDoc, serverTimestamp } from "firebase/firestore";
import { db } from "../firebase";
import { useAuth } from "../hooks/useAuth";
import { isSnowflake } from "../lib/limits";
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

const TAKEN_MESSAGE =
  "This server has already been activated by another account. " +
  "If that wasn't you, ask whoever set the bot up.";

/** Firestore's "the rules said no", as opposed to offline / unavailable. */
function isPermissionDenied(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    (err as { code?: unknown }).code === "permission-denied"
  );
}

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
    const id = serverId.trim();
    if (!id || !user) return;
    setErrorMsg("");

    // The rules only accept a Discord snowflake as the document id. Say so
    // here, in words, rather than after a refused write.
    if (!isSnowflake(id)) {
      setErrorMsg(
        "That doesn't look like a Discord Server ID — it should be a 17–20 digit number."
      );
      setStatus("error");
      return;
    }

    setStatus("saving");
    const ref = doc(db, "serverOwners", id);

    // "Is this server already taken?" Owner records are private: the rules
    // let a signed-in user read their OWN record, or a path where no record
    // exists yet, and refuse anyone else's. So a refusal here is the answer
    // — it is activated, and not by this account.
    try {
      await getDoc(ref);
    } catch (err) {
      setErrorMsg(
        isPermissionDenied(err)
          ? TAKEN_MESSAGE
          : "Couldn't check that server right now. Please try again."
      );
      setStatus("error");
      return;
    }

    try {
      await setDoc(ref, {
        ownerDiscordId: "",
        ownerEmail: user.email,
        firebaseUid: user.uid,
        activatedAt: serverTimestamp(),
        isActive: true,
      });
      setStatus("done");
    } catch (err) {
      // Someone else can claim it between the check and the write.
      setErrorMsg(
        isPermissionDenied(err)
          ? TAKEN_MESSAGE
          : "Failed to activate server. Please try again."
      );
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
              <Button onClick={() => navigate("/me")} className="w-full">
                Go to My Servers
              </Button>
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
  );
}
