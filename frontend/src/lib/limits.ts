/**
 * Client-side mirrors of bounds that firestore.rules enforces.
 *
 * The rules are the authority — these exist only so the dashboard can say
 * something useful ("that is not a server ID") instead of surfacing a bare
 * "Missing or insufficient permissions" after the write is refused. If a
 * value here and the rule ever disagree, the rule wins and this file is the
 * one that is wrong. Each constant names the rule it mirrors.
 */

/**
 * A Discord snowflake: every guild id and channel id.
 * Mirrors `isSnowflake()` in firestore.rules. Document ids under `servers/`
 * and `serverOwners/` must match it.
 */
const SNOWFLAKE = /^[0-9]{15,22}$/;

export function isSnowflake(id: unknown): id is string {
  return typeof id === "string" && SNOWFLAKE.test(id);
}

/** Mirrors `validPlaylist()`: the document id is the name, 1–100 characters. */
export const PLAYLIST_NAME_MAX = 100;

/** Mirrors `validPlaylist()`: at most this many tracks in one playlist. */
export const PLAYLIST_TRACKS_MAX = 1000;

/** Mirrors `validServerUpdate()`: `searchQuery` is 1–500 characters. */
export const SEARCH_QUERY_MAX = 500;
