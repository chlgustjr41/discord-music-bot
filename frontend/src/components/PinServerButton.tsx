import { useEffect, useState } from "react";
import { deleteDoc, doc, onSnapshot, serverTimestamp, setDoc } from "firebase/firestore";
import { db } from "../firebase";
import { useAuth } from "../hooks/useAuth";
import { Button } from "@/components/ui/button";
import { Bookmark, BookmarkCheck } from "lucide-react";

interface Props {
  serverId: string;
  serverName?: string;
}

/**
 * Pin the current session's server to the signed-in user's "My Servers"
 * (/me). Ownership (who activated) is unchanged — this is membership by
 * participation: anyone in the session can pin it for later summon access.
 */
export function PinServerButton({ serverId, serverName }: Props) {
  const { user } = useAuth();
  const [pinned, setPinned] = useState<boolean | null>(null);

  useEffect(() => {
    if (!user) {
      setPinned(null);
      return;
    }
    return onSnapshot(
      doc(db, "users", user.uid, "pinnedServers", serverId),
      (snap) => setPinned(snap.exists()),
      () => setPinned(null)
    );
  }, [user, serverId]);

  if (!user || pinned === null) return null;

  const toggle = async () => {
    const ref = doc(db, "users", user.uid, "pinnedServers", serverId);
    try {
      if (pinned) {
        await deleteDoc(ref);
      } else {
        await setDoc(ref, { serverName: serverName || serverId, pinnedAt: serverTimestamp() });
      }
    } catch {
      /* best-effort */
    }
  };

  return (
    <Button
      variant="ghost"
      size="sm"
      className={`shrink-0 ${pinned ? "text-primary" : "text-muted-foreground hover:text-foreground"}`}
      onClick={toggle}
      title={pinned ? "Remove from My Servers" : "Add to My Servers (/me)"}
    >
      {pinned ? <BookmarkCheck className="h-4 w-4" /> : <Bookmark className="h-4 w-4" />}
    </Button>
  );
}
