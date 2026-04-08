import { useState, useEffect, useRef } from "react";
import { doc, onSnapshot } from "firebase/firestore";
import { db } from "../firebase";
import type { ServerState } from "../types";

export function useServerState(sessionCode: string | undefined) {
  const [serverId, setServerId] = useState<string | null>(null);
  const [state, setState] = useState<ServerState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [sessionExpired, setSessionExpired] = useState(false);
  const initializedRef = useRef(false);

  // Real-time subscription to session code doc (survives refresh, stays in sync)
  useEffect(() => {
    if (!sessionCode) {
      setLoading(false);
      return;
    }

    const unsubscribe = onSnapshot(
      doc(db, "sessionCodes", sessionCode),
      (snap) => {
        if (snap.exists()) {
          setServerId(snap.data().serverId);
          setError(null);
        } else {
          // Session code doc was deleted — code is invalid or expired
          setServerId(null);
          setSessionExpired(true);
          setLoading(false);
        }
      },
      (err) => {
        setError(err.message);
        setLoading(false);
      }
    );

    return unsubscribe;
  }, [sessionCode]);

  // Real-time subscription to server state
  useEffect(() => {
    if (!serverId) return;

    const unsubscribe = onSnapshot(
      doc(db, "servers", serverId),
      (snap) => {
        if (snap.exists()) {
          const data = snap.data() as ServerState;
          setState(data);

          // After initial load, detect if session code changed (new session issued)
          if (initializedRef.current) {
            if (data.sessionCode !== sessionCode) {
              setSessionExpired(true);
            }
          } else {
            initializedRef.current = true;
          }
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
  }, [serverId, sessionCode]);

  return { serverId, state, error, loading, sessionExpired };
}
