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

/**
 * What this panel knows about its own in-flight bot search.
 *
 * `searchQuery`/`searchResults` are single shared fields on `servers/{id}`, so
 * "results arrived" is never by itself evidence that they are OUR results.
 * `ownQuery` is the query we asked the bot for; `observed` records that we
 * actually watched it sitting in the shared field, which is the only moment at
 * which the session is demonstrably working on our behalf.
 */
export interface OwnSearchState {
  ownQuery: string | null;
  observed: boolean;
}

export const IDLE_OWN_SEARCH: OwnSearchState = { ownQuery: null, observed: false };

/**
 * Fold one snapshot of the shared `searchQuery` field into that knowledge.
 *
 * A cleared query says nothing about whose search it answered, so it leaves
 * the state alone. A query that is not ours means somebody else's write
 * overwrote the field: our search is gone, and the answer that follows will be
 * theirs — so the claim is dropped rather than merely un-observed.
 */
export function nextOwnSearchState(
  prev: OwnSearchState,
  incomingQuery: string | null | undefined,
): OwnSearchState {
  if (!incomingQuery) return prev;
  if (!prev.ownQuery) return prev;
  if (incomingQuery === prev.ownQuery) {
    return prev.observed ? prev : { ownQuery: prev.ownQuery, observed: true };
  }
  return IDLE_OWN_SEARCH;
}

export interface AdoptInput {
  mode: ViewMode;
  waiting: boolean;
  ownQuery: string | null;
  observedOwnQuery: boolean;
  incomingQuery: string | null | undefined;
}

/**
 * May this panel render the results currently sitting on the shared document?
 *
 * Shared dashboards follow the session, so any answer is theirs by definition.
 * Solo dashboards may only ever adopt the answer to a query they sent AND
 * watched land — otherwise a solo user's fallback search silently renders
 * whatever a shared user happened to ask for in the meantime.
 */
export function shouldAdoptSharedResults({
  mode,
  waiting,
  ownQuery,
  observedOwnQuery,
  incomingQuery,
}: AdoptInput): boolean {
  if (!waiting) return false;
  if (incomingQuery) return false; // bot still processing
  if (shouldFollowSharedSearch(mode)) return true;
  return ownQuery !== null && observedOwnQuery;
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
