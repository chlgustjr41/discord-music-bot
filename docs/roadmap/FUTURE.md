# Roadmap (deferred — brainstorm before building)

1. **Local audio nodes** — user-hosted Lavalink for low latency; `NodeProvider` second implementation with automatic fallback to the VM node on local-node disconnect.
2. ~~**Dashboard "summon"**~~ — ✅ shipped 2026-07-04: signed-in owners see their servers + previously-visited voice channels on `/activate`; one click summons the bot (`summonRequest` → always-on `SummonWatcher`) and auto-opens the new session dashboard.
3. **Dashboard social features** — Each channel will have record of each user's search logs and manually played tracks to show a leader board of past tracks within the channel session dashboard. The stats could include most played songs, most dragged (reordered) songs, most played artist and such.
4. ~~**Search engine fix**~~ — ✅ shipped 2026-07-04: playlist URL → the URL's video first then the playlist; single-video URL → that track + up to 9 similar (title+author search); results show thumbnails + source badges. Search-as-you-type was already live (200 ms debounce through the Firestore round-trip). Remaining idea folded into #5: Spotify source badge.
5. **Spotify support** — blocked on Spotify API credentials (developer account); revisit when available.
