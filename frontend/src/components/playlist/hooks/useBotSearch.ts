import { useState, useEffect, useRef, useCallback } from "react";
import { doc, updateDoc } from "firebase/firestore";
import { db } from "../../../firebase";
import type { SearchResult } from "../../../types";

interface Args {
  serverId: string;
  /** Live searchResults from server doc subscription (passed from parent). */
  searchResults?: SearchResult[];
  /** Live searchQuery from server doc subscription. Cleared by bot when done. */
  searchQuery?: string | null;
  /** Live searchPlaylistName from server doc subscription. Set by bot when results came from a playlist URL. */
  searchPlaylistName?: string | null;
  /** Timeout in ms for the bot to respond (default 15000). */
  timeoutMs?: number;
}

interface State {
  query: string;
  results: SearchResult[];
  playlistName: string | null;
  loading: boolean;
  error: string;
}

export function useBotSearch({
  serverId,
  searchResults,
  searchQuery,
  searchPlaylistName,
  timeoutMs = 15000,
}: Args) {
  const [state, setState] = useState<State>({
    query: "",
    results: [],
    playlistName: null,
    loading: false,
    error: "",
  });
  const waitingForResults = useRef(false);
  const lastSentQuery = useRef("");

  // Consume bot-returned results when we are the requester
  useEffect(() => {
    if (!waitingForResults.current) return;
    if (searchQuery) return; // bot still processing
    if (searchResults && searchResults.length > 0) {
      setState((s) => ({
        ...s,
        results: searchResults,
        playlistName: searchPlaylistName ?? null,
        loading: false,
        error: "",
      }));
    } else {
      setState((s) => ({
        ...s,
        results: [],
        playlistName: null,
        loading: false,
        error: "No results found.",
      }));
    }
    waitingForResults.current = false;
  }, [searchResults, searchQuery, searchPlaylistName]);

  // Timeout
  useEffect(() => {
    if (!state.loading) return;
    const t = setTimeout(() => {
      if (waitingForResults.current) {
        waitingForResults.current = false;
        setState((s) => ({
          ...s,
          loading: false,
          error: "Search timed out. Make sure the bot is connected.",
        }));
      }
    }, timeoutMs);
    return () => clearTimeout(t);
  }, [state.loading, timeoutMs]);

  const search = useCallback(
    async (q: string) => {
      const trimmed = q.trim();
      if (!trimmed) return;
      if (trimmed === lastSentQuery.current && state.results.length > 0) return;
      lastSentQuery.current = trimmed;
      setState({
        query: trimmed,
        results: [],
        playlistName: null,
        loading: true,
        error: "",
      });
      waitingForResults.current = true;
      try {
        await updateDoc(doc(db, "servers", serverId), {
          searchQuery: trimmed,
          searchResults: [],
        });
      } catch {
        waitingForResults.current = false;
        setState({
          query: trimmed,
          results: [],
          playlistName: null,
          loading: false,
          error: "Search failed.",
        });
      }
    },
    [serverId, state.results.length]
  );

  const clear = useCallback(() => {
    waitingForResults.current = false;
    lastSentQuery.current = "";
    setState({
      query: "",
      results: [],
      playlistName: null,
      loading: false,
      error: "",
    });
  }, []);

  return { ...state, search, clear };
}
