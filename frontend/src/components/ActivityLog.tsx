import { useSharedSection } from "../hooks/useSharedSection";
import type { LogEntry } from "../hooks/useActivityToasts";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ResizableList } from "./ResizableList";
import { ScrollText, ChevronDown } from "lucide-react";

interface Props {
  entries: LogEntry[];
}

function formatTime(date: Date) {
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function ActivityLog({ entries }: Props) {
  const [expanded, setExpanded] = useSharedSection("activity", false);

  return (
    <Card>
      <Collapsible open={expanded} onOpenChange={setExpanded}>
        <CardHeader className="pb-3">
          <CollapsibleTrigger className="flex w-full items-center gap-2">
            <CardTitle className="flex items-center gap-2 text-lg">
              <ScrollText className="h-4 w-4" />
              Log
            </CardTitle>
            <Badge variant="secondary" className="ml-auto mr-2">
              {entries.length}
            </Badge>
            <ChevronDown
              className={`h-4 w-4 text-muted-foreground transition-transform ${expanded ? "rotate-180" : ""}`}
            />
          </CollapsibleTrigger>
        </CardHeader>
        <CollapsibleContent>
          <div className="px-4 pb-4">
            {entries.length === 0 ? (
              <p className="py-4 text-center text-sm text-muted-foreground">
                No activity yet.
              </p>
            ) : (
              <ResizableList defaultHeight={250} minHeight={100} maxHeight={500}>
                <ul className="space-y-0.5">
                  {entries.map((entry) => (
                    <li
                      key={entry.id}
                      className="flex gap-3 rounded-md px-2 py-1.5 text-sm hover:bg-muted/50"
                    >
                      <span className="shrink-0 text-xs text-muted-foreground/60 font-mono pt-0.5">
                        {formatTime(entry.timestamp)}
                      </span>
                      <span className="min-w-0 break-words">{entry.message}</span>
                    </li>
                  ))}
                </ul>
              </ResizableList>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </Card>
  );
}
