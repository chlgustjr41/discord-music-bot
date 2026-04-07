import { useState, useEffect } from "react";
import { doc, getDoc, onSnapshot } from "firebase/firestore";
import { db } from "../firebase";
import type { ServerState } from "../types";

export function useServerState(sessionCode: string | undefined) {
  const [serverId, setServerId] = useState<string | null>(null);
  const [state, setState] = useState<ServerState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Resolve session code to server ID
  useEffect(() => {
    if (!sessionCode) return;
    getDoc(doc(db, "sessionCodes", sessionCode)).then((snap) => {
      if (snap.exists()) {
        setServerId(snap.data().serverId);
      } else {
        setError("Invalid or expired session code.");
        setLoading(false);
      }
    });
  }, [sessionCode]);

  // Subscribe to server state
  useEffect(() => {
    if (!serverId) return;
    const unsubscribe = onSnapshot(
      doc(db, "servers", serverId),
      (snap) => {
        if (snap.exists()) {
          setState(snap.data() as ServerState);
        } else {
          setError("Server not found.");
        }
        setLoading(false);
      },
      (err) => {
        setError(err.message);
        setLoading(false);
      }
    );
    return unsubscribe;
  }, [serverId]);

  return { serverId, state, error, loading };
}
