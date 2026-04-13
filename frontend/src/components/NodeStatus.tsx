import { useEffect, useState } from "react";
import { doc, onSnapshot, updateDoc } from "firebase/firestore";
import { db } from "../firebase";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from "@/components/ui/dialog";
import { Loader2, Wifi, WifiOff, Server } from "lucide-react";

interface LocalNode {
  url: string;
  password: string;
  connected: boolean;
  connectedAt: unknown;
}

interface Props {
  serverId: string;
}

export function NodeStatus({ serverId }: Props) {
  const [localNode, setLocalNode] = useState<LocalNode | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [password, setPassword] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [error, setError] = useState("");

  // Subscribe to serverOwners for local node state
  useEffect(() => {
    const unsub = onSnapshot(
      doc(db, "serverOwners", serverId),
      (snap) => {
        const data = snap.data();
        const node = data?.localNode ?? null;
        setLocalNode(node);
        // Clear loading states when node connects successfully
        if (node?.connected) {
          setConnecting(false);
        }
      },
      () => setLocalNode(null),
    );
    return unsub;
  }, [serverId, connecting]);

  // Timeout for connect/disconnect operations
  useEffect(() => {
    if (!connecting && !disconnecting) return;
    const timeout = setTimeout(() => {
      if (connecting) {
        setConnecting(false);
        setError("Connection timed out. Make sure the bot is connected and the local node is running.");
      }
      if (disconnecting) {
        setDisconnecting(false);
      }
    }, 20000);
    return () => clearTimeout(timeout);
  }, [connecting, disconnecting]);

  const handleConnect = async () => {
    const trimmedUrl = url.trim();
    const trimmedPassword = password.trim();
    if (!trimmedUrl || !trimmedPassword) return;

    setConnecting(true);
    setError("");

    try {
      await updateDoc(doc(db, "servers", serverId), {
        localNodeRequest: {
          action: "connect",
          url: trimmedUrl,
          password: trimmedPassword,
        },
      });
    } catch {
      setError("Failed to send connect request.");
      setConnecting(false);
    }
  };

  const handleDisconnect = async () => {
    setDisconnecting(true);
    setError("");

    try {
      await updateDoc(doc(db, "servers", serverId), {
        localNodeRequest: { action: "disconnect" },
      });
      // Optimistic: clear local state after a short delay
      setTimeout(() => setDisconnecting(false), 3000);
    } catch {
      setError("Failed to send disconnect request.");
      setDisconnecting(false);
    }
  };

  const openDialog = () => {
    setError("");
    setConnecting(false);
    setDisconnecting(false);
    // Pre-fill URL from saved config if available
    if (localNode) {
      setUrl(localNode.url);
      setPassword(localNode.password);
    }
    setDialogOpen(true);
  };

  return (
    <>
      <button onClick={openDialog} className="focus:outline-none">
        {localNode?.connected ? (
          <Badge variant="outline" className="gap-1 text-xs cursor-pointer hover:bg-muted/50 transition-colors">
            <span className="h-1.5 w-1.5 rounded-full bg-green-500" />
            Local Node
          </Badge>
        ) : localNode ? (
          <Badge variant="outline" className="gap-1 text-xs cursor-pointer hover:bg-muted/50 transition-colors">
            <span className="h-1.5 w-1.5 rounded-full bg-yellow-500 animate-pulse" />
            Reconnecting
          </Badge>
        ) : (
          <Badge variant="secondary" className="gap-1 text-xs cursor-pointer hover:bg-muted transition-colors">
            Cloud
          </Badge>
        )}
      </button>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Server className="h-4 w-4" />
              Audio Backend
            </DialogTitle>
            <DialogDescription>
              Connect a local Lavalink node for lower latency audio.
              Run the setup from{" "}
              <a
                href="https://github.com/chlgustjr41/jacky-music-local"
                target="_blank"
                rel="noopener noreferrer"
                className="underline underline-offset-2 hover:text-foreground"
              >
                jacky-music-local
              </a>{" "}
              and enter the tunnel URL and password below.
            </DialogDescription>
          </DialogHeader>

          {/* Current status */}
          <div className={`flex items-center gap-3 rounded-lg border p-3 ${
            localNode?.connected
              ? "border-green-500/30 bg-green-500/5"
              : localNode
                ? "border-yellow-500/30 bg-yellow-500/5"
                : "border-muted"
          }`}>
            {localNode?.connected ? (
              <Wifi className="h-5 w-5 shrink-0 text-green-500" />
            ) : localNode ? (
              <Loader2 className="h-5 w-5 shrink-0 text-yellow-500 animate-spin" />
            ) : (
              <WifiOff className="h-5 w-5 shrink-0 text-muted-foreground" />
            )}
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium">
                {localNode?.connected
                  ? "Local Node Connected"
                  : localNode
                    ? "Local Node Reconnecting..."
                    : "Using Cloud Backend"}
              </p>
              {localNode && (
                <p className="truncate text-xs text-muted-foreground mt-0.5">
                  {localNode.url}
                </p>
              )}
            </div>
          </div>

          {!localNode ? (
            <>
              {/* Connect form */}
              <div className="space-y-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">
                    Tunnel URL
                  </label>
                  <Input
                    type="text"
                    value={url}
                    onChange={(e) => { setUrl(e.target.value); setError(""); }}
                    placeholder="https://abc123.trycloudflare.com"
                    onKeyDown={(e) => { if (e.key === "Enter") handleConnect(); }}
                  />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">
                    Password
                  </label>
                  <Input
                    type="password"
                    value={password}
                    onChange={(e) => { setPassword(e.target.value); setError(""); }}
                    placeholder="Lavalink password"
                    onKeyDown={(e) => { if (e.key === "Enter") handleConnect(); }}
                  />
                </div>
              </div>

              {error && <p className="text-sm text-destructive">{error}</p>}

              <DialogFooter>
                <DialogClose render={<Button variant="outline" />}>
                  Cancel
                </DialogClose>
                <Button
                  onClick={handleConnect}
                  disabled={connecting || !url.trim() || !password.trim()}
                >
                  {connecting ? (
                    <>
                      <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                      Connecting...
                    </>
                  ) : (
                    <>
                      <Wifi className="mr-1.5 h-4 w-4" />
                      Connect
                    </>
                  )}
                </Button>
              </DialogFooter>
            </>
          ) : (
            <>
              {error && <p className="text-sm text-destructive">{error}</p>}

              <DialogFooter>
                <DialogClose render={<Button variant="outline" />}>
                  Close
                </DialogClose>
                <Button
                  variant="destructive"
                  onClick={handleDisconnect}
                  disabled={disconnecting}
                >
                  {disconnecting ? (
                    <>
                      <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                      Disconnecting...
                    </>
                  ) : (
                    <>
                      <WifiOff className="mr-1.5 h-4 w-4" />
                      Disconnect
                    </>
                  )}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
