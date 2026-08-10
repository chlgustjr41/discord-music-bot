import { useState } from "react";
import { useIdentity } from "../lib/identity";
import { NicknameDialog } from "./NicknameDialog";
import { UserRound } from "lucide-react";

/**
 * FUTURE #3 — who you are on this dashboard. Anyone can set a nickname
 * (stored in this browser) or sign in with Google; either way queue adds,
 * drags, and searches get attributed on the leaderboard.
 *
 * The editor itself lives in NicknameDialog, because clicking your own avatar
 * in the presence bar opens the same one.
 */
export function IdentityChip() {
  const identity = useIdentity();
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors ${
          identity.named
            ? "border-primary/30 bg-primary/10 text-primary"
            : "border-input text-muted-foreground hover:text-foreground"
        }`}
        title="Set who you are for the channel leaderboard"
      >
        <UserRound className="h-3 w-3" />
        <span className="max-w-28 truncate">{identity.named ? identity.name : "Set name"}</span>
      </button>

      {open && <NicknameDialog onClose={() => setOpen(false)} />}
    </>
  );
}
