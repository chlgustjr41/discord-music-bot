import { useState } from "react";
import { createPortal } from "react-dom";
import { useAuth } from "../hooks/useAuth";
import { setNickname, useIdentity } from "../lib/identity";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Check, LogIn, LogOut } from "lucide-react";

/**
 * "Who are you?" — the one nickname editor.
 *
 * Lifted out of IdentityChip when the presence bar gained a second way in
 * (clicking your own avatar). Two entry points to one dialog; duplicating the
 * dialog would have meant two places to keep the 32-character cap, the
 * signed-in branch and the sign-in fallback in step with each other.
 *
 * Callers mount it only while it is open — `{open && <NicknameDialog … />}` —
 * so the draft starts from the CURRENT nickname on every open rather than
 * from whatever it was the first time the header rendered.
 */
export function NicknameDialog({ onClose }: { onClose: () => void }) {
  const identity = useIdentity();
  const { signInWithGoogle, logout } = useAuth();
  const [draft, setDraft] = useState(identity.nickname);

  const save = () => {
    setNickname(draft);
    onClose();
  };

  return createPortal(
    <div
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm space-y-4 rounded-lg border bg-card p-4 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div>
          <p className="font-semibold">Who are you?</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Your name is attached to the tracks you add, your searches, and queue
            drags — that's what the Channel Leaderboard ranks. Stay anonymous and
            you're just "Web User" (not ranked).
          </p>
          {identity.named && (
            <p className="mt-2 text-xs text-muted-foreground">
              Shown as <span className="font-medium">{identity.name}</span>
              {identity.viaAccount ? " — from your Google account" : " — your nickname"}
            </p>
          )}
        </div>

        {identity.signedIn && (
          <div className="flex items-center justify-between gap-2 rounded-md bg-muted/50 px-3 py-2">
            <span className="truncate text-sm">
              Signed in as{" "}
              <span className="font-medium">{identity.accountName || "your account"}</span>
            </span>
            <Button size="sm" variant="ghost" onClick={() => logout()}>
              <LogOut className="mr-1 h-3 w-3" />
              Sign out
            </Button>
          </div>
        )}

        {/* Offered whether or not you are signed in. It used to be
            anonymous-only, which made renaming impossible for exactly the
            people who have an avatar in the presence bar to rename. */}
        <div className="space-y-2">
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={
              identity.signedIn
                ? "Nickname (shown instead of your account name)"
                : "Nickname (stored in this browser)"
            }
            maxLength={32}
            onKeyDown={(e) => e.key === "Enter" && save()}
          />
          <Button size="sm" className="w-full" onClick={save}>
            <Check className="mr-1 h-3 w-3" />
            {draft.trim() ? "Use this nickname" : "Clear nickname"}
          </Button>
        </div>

        {!identity.signedIn && (
          <>
            <div className="flex items-center gap-2">
              <div className="h-px flex-1 bg-border" />
              <span className="text-[10px] uppercase text-muted-foreground">or</span>
              <div className="h-px flex-1 bg-border" />
            </div>
            <Button
              size="sm"
              variant="outline"
              className="w-full"
              onClick={async () => {
                await signInWithGoogle();
                onClose();
              }}
            >
              <LogIn className="mr-1 h-3 w-3" />
              Sign in with Google
            </Button>
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
