import { useState, useEffect } from "react";
import {
  collection,
  query,
  orderBy,
  limit,
  onSnapshot,
  doc,
  updateDoc,
  deleteDoc,
  getDocs,
} from "firebase/firestore";
import { db } from "../firebase";
import { useSharedSection } from "../hooks/useSharedSection";
import type { CommandHistoryEntry } from "../types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ResizableList } from "./ResizableList";
import {
  Terminal,
  ChevronDown,
  RotateCcw,
  Clock,
  Check,
  Trash2,
  Mic,
} from "lucide-react";

interface Props {
  serverId: string;
}

export function CommandHistory({ serverId }: Props) {
  const [commands, setCommands] = useState<CommandHistoryEntry[]>([]);
  const [expanded, setExpanded] = useSharedSection("commands", false);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!expanded) return;
    const q = query(
      collection(db, "servers", serverId, "commandHistory"),
      orderBy("timestamp", "desc"),
      limit(30)
    );
    const unsub = onSnapshot(q, (snap) => {
      setCommands(
        snap.docs.map(
          (d) => ({ id: d.id, ...d.data() } as CommandHistoryEntry)
        )
      );
    });
    return () => unsub();
  }, [expanded, serverId]);

  const retrigger = async (cmd: CommandHistoryEntry) => {
    await updateDoc(doc(db, "servers", serverId), {
      retriggerCommand: {
        command: cmd.command,
        args: cmd.args,
        triggeredAt: new Date().toISOString(),
      },
    });
  };

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => {
    if (selected.size === commands.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(commands.map((c) => c.id)));
    }
  };

  const deleteSelected = async () => {
    const ids = Array.from(selected);
    for (const id of ids) {
      await deleteDoc(doc(db, "servers", serverId, "commandHistory", id));
    }
    setSelected(new Set());
  };

  const clearAll = async () => {
    const snap = await getDocs(
      collection(db, "servers", serverId, "commandHistory")
    );
    for (const d of snap.docs) {
      await deleteDoc(d.ref);
    }
    setSelected(new Set());
  };

  const formatTime = (ts: unknown) => {
    if (!ts) return "";
    try {
      const d =
        ts && typeof ts === "object" && "toDate" in ts
          ? (ts as { toDate: () => Date }).toDate()
          : new Date(ts as string);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
      return "";
    }
  };

  const formatCommand = (cmd: CommandHistoryEntry) => {
    const prefix = `j!${cmd.command}`;
    return cmd.args ? `${prefix} ${cmd.args}` : prefix;
  };

  const retriggerable = new Set([
    "play",
    "skip",
    "pause",
    "resume",
    "loop",
    "volume",
  ]);

  return (
    <Card>
      <Collapsible open={expanded} onOpenChange={setExpanded}>
        <CardHeader className="pb-3">
          <CollapsibleTrigger className="flex w-full items-center gap-2">
            <CardTitle className="flex items-center gap-2 text-lg">
              <Terminal className="h-4 w-4" />
              Command History
            </CardTitle>
            <ChevronDown
              className={`ml-auto h-4 w-4 text-muted-foreground transition-transform ${expanded ? "rotate-180" : ""}`}
            />
          </CollapsibleTrigger>
        </CardHeader>
        <CollapsibleContent>
          <CardContent className="space-y-2 pt-0">
            {commands.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-4 text-muted-foreground">
                <Clock className="mb-1 h-6 w-6" />
                <p className="text-sm">No commands yet.</p>
              </div>
            ) : (
              <>
                {/* Toolbar */}
                <div className="flex items-center gap-2 flex-wrap">
                  <Button size="sm" variant="outline" onClick={selectAll}>
                    {selected.size === commands.length
                      ? "Deselect All"
                      : "Select All"}
                  </Button>
                  {selected.size > 0 && (
                    <>
                      <Badge variant="secondary">
                        {selected.size} selected
                      </Badge>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-destructive hover:text-destructive"
                        onClick={deleteSelected}
                      >
                        <Trash2 className="mr-1 h-3 w-3" />
                        Delete
                      </Button>
                    </>
                  )}
                  <Button
                    size="sm"
                    variant="ghost"
                    className="ml-auto text-destructive hover:text-destructive"
                    onClick={clearAll}
                  >
                    <Trash2 className="mr-1 h-3 w-3" />
                    Clear All
                  </Button>
                </div>

                <ResizableList defaultHeight={288} minHeight={100}>
                  <ul className="space-y-1">
                    {commands.map((cmd) => {
                      const isSelected = selected.has(cmd.id);
                      return (
                        <li
                          key={cmd.id}
                          onClick={() => toggleSelect(cmd.id)}
                          className={`flex items-center gap-2 rounded-md p-2 cursor-pointer transition-colors ${
                            isSelected
                              ? "bg-primary/10 border border-primary/30"
                              : "hover:bg-muted/50"
                          }`}
                        >
                          <div
                            className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border ${
                              isSelected
                                ? "border-primary bg-primary text-primary-foreground"
                                : "border-muted-foreground/30"
                            }`}
                          >
                            {isSelected && <Check className="h-3 w-3" />}
                          </div>
                          <div className="flex-1 min-w-0">
                            {cmd.source === "voice" ? (
                              <div className="flex min-w-0 flex-col gap-0.5 text-sm font-mono">
                                <span className="flex items-center gap-1.5">
                                  <Mic className="h-3 w-3 shrink-0 text-primary" />
                                  <Badge
                                    variant="outline"
                                    className="px-1 py-0 text-[10px]"
                                  >
                                    Voice
                                  </Badge>
                                  <span className="truncate italic">
                                    &quot;{cmd.transcript}&quot;
                                  </span>
                                </span>
                                <span className="truncate text-xs text-muted-foreground">
                                  → {formatCommand(cmd)}
                                </span>
                              </div>
                            ) : (
                              <p className="truncate text-sm font-mono">
                                {formatCommand(cmd)}
                              </p>
                            )}
                            <p className="text-xs text-muted-foreground">
                              {cmd.user}
                              {cmd.callCount > 1 && ` · ${cmd.callCount}x`}
                              {" · "}
                              {formatTime(cmd.timestamp)}
                            </p>
                          </div>
                          {retriggerable.has(cmd.command) && (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={(e) => {
                                e.stopPropagation();
                                retrigger(cmd);
                              }}
                              title="Re-run this command"
                            >
                              <RotateCcw className="h-3 w-3" />
                            </Button>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                </ResizableList>
              </>
            )}
          </CardContent>
        </CollapsibleContent>
      </Collapsible>
    </Card>
  );
}
