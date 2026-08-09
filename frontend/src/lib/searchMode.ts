/**
 * Which search path a dashboard uses, and what happens when the private one
 * is unavailable.
 *
 * Search is a BOT capability: the client writes `searchQuery` to the shared
 * server document and the bot writes results back, so a search in shared mode
 * is visible to everyone in the session by construction.
 *
 * Solo mode therefore tries a client-side endpoint first. That endpoint
 * (functions/searchYouTube) is not deployed today, so the fallback is the
 * live path rather than a theoretical one — solo search works, it just is not
 * yet private. Deploying the function makes it private with no change here.
 */

import type { ViewMode } from "./presence";

export type SearchVia = "bot" | "local" | "bot-fallback";

export interface SearchDeps<T> {
  local: (query: string) => Promise<T[]>;
  bot: (query: string) => Promise<void>;
}

export interface SearchOutcome<T> {
  via: SearchVia;
  results: T[] | null;
}

/** Solo dashboards keep their own results: another person searching must not
 *  replace what you are looking at. */
export function shouldFollowSharedSearch(mode: ViewMode): boolean {
  return mode === "shared";
}

export async function runSearch<T>(
  mode: ViewMode,
  query: string,
  deps: SearchDeps<T>,
): Promise<SearchOutcome<T>> {
  if (mode === "shared") {
    await deps.bot(query);
    return { via: "bot", results: null };
  }
  try {
    return { via: "local", results: await deps.local(query) };
  } catch {
    // Deliberately not swallowed silently: the caller toasts once so the user
    // knows this search became visible to the session.
    await deps.bot(query);
    return { via: "bot-fallback", results: null };
  }
}
