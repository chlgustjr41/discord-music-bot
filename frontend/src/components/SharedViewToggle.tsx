import { Button } from "@/components/ui/button";
import { Users, UserRound } from "lucide-react";
import type { ViewMode } from "../lib/presence";

interface Props {
  mode: ViewMode;
  signedIn: boolean;
  onChange: (m: ViewMode) => void;
}

/**
 * Opt in to being visible to everyone else on this session dashboard.
 *
 * Anonymous visitors never see the control: they cannot publish presence at
 * all (shouldPublish), so offering them a choice would be a lie.
 */
export function SharedViewToggle({ mode, signedIn, onChange }: Props) {
  if (!signedIn) return null;

  const shared = mode === "shared";

  return (
    <Button
      variant="ghost"
      size="sm"
      className={`shrink-0 ${shared ? "text-primary" : "text-muted-foreground hover:text-foreground"}`}
      onClick={() => onChange(shared ? "solo" : "shared")}
      title={
        shared
          ? "Shared view is on: your name, colour, and pointer are visible to everyone in this session. Click to go solo."
          : "Solo view: nobody sees you here. Click to share your name, colour, and pointer with everyone in this session."
      }
    >
      {shared ? <Users className="h-4 w-4" /> : <UserRound className="h-4 w-4" />}
    </Button>
  );
}
