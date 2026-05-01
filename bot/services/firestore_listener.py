import asyncio
import datetime
from datetime import timezone
import logging
import re
import wavelink

log = logging.getLogger(__name__)

GCP_NODE_ID = "gcp-primary"


def _get_gcp_node() -> wavelink.Node | None:
    for node in wavelink.Pool.nodes.values():
        if node.identifier == GCP_NODE_ID and node.status == wavelink.NodeStatus.CONNECTED:
            return node
    return None

URL_RE = re.compile(r"^https?://")


class FirestoreListener:
    """Watches a server's Firestore doc for changes from the web app."""

    def __init__(self, bot, fs, server_id: str):
        self.bot = bot
        self.fs = fs
        self.server_id = server_id
        self._unsubscribe = None
        self._last_state = None
        self._resolving = False  # Prevent re-entry when we update queue ourselves

    def start(self):
        doc_ref = self.fs.db.collection("servers").document(self.server_id)
        self._unsubscribe = doc_ref.on_snapshot(self._on_snapshot)

    def stop(self):
        if self._unsubscribe:
            self._unsubscribe.unsubscribe()
            self._unsubscribe = None

    def _on_snapshot(self, doc_snapshot, changes, read_time):
        for doc in doc_snapshot:
            new_state = doc.to_dict()
            if self._last_state is None:
                self._last_state = new_state
                continue

            old = self._last_state
            self._last_state = new_state

            asyncio.run_coroutine_threadsafe(
                self._handle_changes(old, new_state),
                self.bot.loop,
            )

    async def _handle_changes(self, old: dict, new: dict):
        guild = self.bot.get_guild(int(self.server_id))
        if not guild:
            log.warning(f"Guild {self.server_id} not found, skipping change handling")
            return

        # --- Search request from web app ---
        old_query = old.get("searchQuery")
        new_query = new.get("searchQuery")
        if new_query and new_query != old_query:
            log.info(f"Search request from web: '{new_query}'")
            await self._handle_search(new_query)
            return

        # --- Local node request from web app ---
        old_node_req = old.get("localNodeRequest")
        new_node_req = new.get("localNodeRequest")
        if new_node_req and new_node_req != old_node_req:
            log.info(f"Local node request from web: {new_node_req}")
            await self._handle_local_node_request(new_node_req)
            self.fs.update_server_state(self.server_id, {"localNodeRequest": None})
            return

        # --- Retrigger command from web app ---
        old_retrigger = old.get("retriggerCommand")
        new_retrigger = new.get("retriggerCommand")
        if new_retrigger and new_retrigger != old_retrigger:
            log.info(f"Retrigger command from web: {new_retrigger}")
            await self._handle_retrigger(new_retrigger)
            # Clear the retrigger field
            self.fs.update_server_state(self.server_id, {"retriggerCommand": None})
            return

        # --- Resolve unresolved queue items (URLs added from web) ---
        queue_grew = False
        if not self._resolving:
            old_queue = old.get("queue", [])
            new_queue = new.get("queue", [])
            if len(new_queue) > len(old_queue):
                queue_grew = True
                await self._resolve_new_queue_items(new_queue)

        player = guild.voice_client
        if not player:
            return

        # Auto-play: if queue grew and nothing is currently playing, start playback
        if queue_grew and not player.playing and not new.get("currentTrack"):
            log.info(f"Queue grew while idle — auto-starting playback in guild {self.server_id}")
            playback_cog = self.bot.get_cog("Playback")
            if playback_cog:
                await playback_cog.play_next(player, guild.id)
            return

        # Volume change from web
        if old.get("volume") != new.get("volume"):
            await player.set_volume(new.get("volume", 80))

        # Pause/resume from web
        if old.get("isPaused") != new.get("isPaused"):
            # If unpausing while nothing is playing, start the queue
            if not new.get("isPaused") and not player.playing and not new.get("currentTrack"):
                queue = new.get("queue", [])
                if queue:
                    log.info(f"Play pressed while idle with queue — starting playback in guild {self.server_id}")
                    playback_cog = self.bot.get_cog("Playback")
                    if playback_cog:
                        await playback_cog.play_next(player, guild.id)
                    return
            await player.pause(new.get("isPaused", False))

        # Seek from web
        old_seek = old.get("seekPosition")
        new_seek = new.get("seekPosition")
        if new_seek is not None and new_seek != old_seek:
            log.info(f"Seek request from web: {new_seek}s in guild {self.server_id}")
            await player.seek(int(new_seek) * 1000)
            # Update startedAt to reflect the new position
            current = new.get("currentTrack")
            if current:
                new_started = datetime.datetime.now(timezone.utc) - datetime.timedelta(seconds=int(new_seek))
                self.fs.update_server_state(self.server_id, {
                    "currentTrack": {**current, "startedAt": new_started.isoformat()},
                    "seekPosition": None,
                })

        # Skip (web sets currentTrack to None while isPlaying is True)
        if (old.get("currentTrack") is not None and
                new.get("currentTrack") is None and
                new.get("isPlaying", False)):
            await player.stop()

    async def _resolve_new_queue_items(self, queue: list):
        """Resolve metadata for queue items that have a URL as their title.
        If a URL points to a playlist, expand it into individual tracks."""
        updated = False
        new_queue = []
        for track in queue:
            title = track.get("title", "")
            url = track.get("url", "")
            # If title looks like a URL, it needs resolution
            if URL_RE.match(title) and url:
                try:
                    results = await wavelink.Playable.search(url, node=_get_gcp_node())

                    # Playlist URL — expand all tracks
                    if isinstance(results, wavelink.Playlist) and len(results.tracks) > 1:
                        log.info(f"Expanding playlist '{results.name}' with {len(results.tracks)} tracks")
                        for t in results.tracks:
                            thumb = getattr(t, "artwork", "") or ""
                            vid = t.identifier or ""
                            if not thumb and vid:
                                thumb = f"https://img.youtube.com/vi/{vid}/mqdefault.jpg"
                            new_queue.append({
                                "title": t.title or url,
                                "artist": t.author or "",
                                "thumbnail": thumb,
                                "duration": t.length // 1000 if t.length else 0,
                                "url": t.uri or url,
                                "requestedBy": track.get("requestedBy", "Web User"),
                            })
                        updated = True
                        continue

                    # Single track resolution
                    playable = None
                    if isinstance(results, wavelink.Playlist):
                        playable = results.tracks[0] if results.tracks else None
                    elif isinstance(results, list) and results:
                        playable = results[0]
                    elif isinstance(results, wavelink.Playable):
                        playable = results

                    if playable:
                        p_thumb = getattr(playable, "artwork", "") or ""
                        p_vid = playable.identifier or ""
                        if not p_thumb and p_vid:
                            p_thumb = f"https://img.youtube.com/vi/{p_vid}/mqdefault.jpg"
                        new_queue.append({
                            **track,
                            "title": playable.title or title,
                            "artist": playable.author or "",
                            "thumbnail": p_thumb,
                            "duration": playable.length // 1000 if playable.length else 0,
                            "url": playable.uri or url,
                        })
                        updated = True
                        continue
                except Exception as e:
                    log.error(f"Failed to resolve queue item '{url}': {e}")

            new_queue.append(track)

        if updated:
            self._resolving = True
            try:
                self.fs.update_server_state(self.server_id, {"queue": new_queue})
            finally:
                self._resolving = False

    async def _handle_retrigger(self, retrigger: dict):
        """Re-execute a command from the web app."""
        command = retrigger.get("command", "")
        args = retrigger.get("args", "")
        guild = self.bot.get_guild(int(self.server_id))
        if not guild:
            return

        player = guild.voice_client
        if not player:
            return

        playback_cog = self.bot.get_cog("Playback")
        if not playback_cog:
            return

        # Log the retrigger as a command from web
        self.fs.log_command(self.server_id, command, args, "Web User", "web")

        if command == "play" and args:
            try:
                results = await wavelink.Playable.search(args, node=_get_gcp_node())
                if not results:
                    return

                # Handle playlist — add all tracks
                if isinstance(results, wavelink.Playlist) and len(results.tracks) > 1:
                    all_track_data = []
                    for t in results.tracks:
                        t_thumb = getattr(t, "artwork", "") or ""
                        if not t_thumb and t.identifier:
                            t_thumb = f"https://img.youtube.com/vi/{t.identifier}/mqdefault.jpg"
                        all_track_data.append({
                            "title": t.title,
                            "artist": t.author,
                            "url": t.uri or args,
                            "thumbnail": t_thumb,
                            "duration": t.length // 1000 if t.length else 0,
                            "requestedBy": "Web User",
                        })

                    if not player.playing:
                        # Play first track immediately, queue the rest
                        first = all_track_data[0]
                        self.fs.set_current_track(self.server_id, {
                            **first,
                            "startedAt": datetime.datetime.now(timezone.utc).isoformat(),
                        })
                        playback_cog = self.bot.get_cog("Playback")
                        if playback_cog:
                            first_result = results.tracks[0]
                            try:
                                await player.play(first_result)
                                self.fs.update_server_state(self.server_id, {"isPlaying": True})
                                if guild.id not in playback_cog.history_buffer:
                                    playback_cog.history_buffer[guild.id] = []
                                    playback_cog.session_start[guild.id] = datetime.datetime.now(timezone.utc)
                                playback_cog.history_buffer[guild.id].append({
                                    **first,
                                    "playedAt": datetime.datetime.now(timezone.utc).isoformat(),
                                })
                                playback_cog.cancel_idle_timer(guild.id)
                            except Exception as e:
                                log.error(f"Retrigger play first playlist track failed: {e}")
                        rest = all_track_data[1:]
                    else:
                        rest = all_track_data

                    for td in rest:
                        self.fs.add_to_queue(self.server_id, td)

                    log.info(f"Retrigger: added {len(all_track_data)} playlist tracks")
                    return

                # Single track
                playable = None
                if isinstance(results, wavelink.Playlist):
                    playable = results.tracks[0] if results.tracks else None
                elif isinstance(results, list) and results:
                    playable = results[0]
                elif isinstance(results, wavelink.Playable):
                    playable = results

                if playable:
                    thumb = getattr(playable, "artwork", "") or ""
                    if not thumb and playable.identifier:
                        thumb = f"https://img.youtube.com/vi/{playable.identifier}/mqdefault.jpg"
                    track_data = {
                        "title": playable.title,
                        "artist": playable.author,
                        "url": playable.uri or args,
                        "thumbnail": thumb,
                        "duration": playable.length // 1000 if playable.length else 0,
                        "requestedBy": "Web User",
                    }
                    self.fs.add_to_queue(self.server_id, track_data)
            except Exception as e:
                log.error(f"Retrigger play failed: {e}")
        elif command == "skip":
            if player.playing:
                await player.stop()
        elif command == "pause":
            if player.playing:
                await player.pause(True)
                self.fs.update_server_state(self.server_id, {"isPaused": True})
        elif command == "resume":
            if player.paused:
                await player.pause(False)
                self.fs.update_server_state(self.server_id, {"isPaused": False})
        elif command == "loop":
            state = self.fs.get_server_state(self.server_id)
            current = state.get("loopMode", "off") if state else "off"
            cycle = {"off": "track", "track": "queue", "queue": "off"}
            new_mode = cycle.get(current, "off")
            self.fs.update_server_state(self.server_id, {"loopMode": new_mode})
        elif command == "volume" and args:
            try:
                vol = max(0, min(100, int(args)))
                await player.set_volume(vol)
                self.fs.update_server_state(self.server_id, {"volume": vol})
            except ValueError:
                pass

    async def _handle_local_node_request(self, request: dict):
        """Handle a local node connect/disconnect request from the web app."""
        action = request.get("action")
        guild_id = int(self.server_id)
        localnode_cog = self.bot.get_cog("LocalNode")
        if not localnode_cog:
            log.error("LocalNode cog not loaded, cannot handle local node request")
            return

        if action == "connect":
            url = request.get("url", "")
            password = request.get("password", "")
            if not url or not password:
                log.warning("Local node connect request missing url or password")
                return
            err = await localnode_cog.connect_node(guild_id, url, password)
            if err:
                log.error(f"Web local node connect failed for guild {guild_id}: {err}")
            else:
                log.info(f"Web local node connected for guild {guild_id}")
        elif action == "disconnect":
            await localnode_cog.disconnect_node(guild_id)
            log.info(f"Web local node disconnected for guild {guild_id}")

    async def _handle_search(self, query: str):
        """Search via Lavalink and write results back to Firestore."""
        try:
            log.info(f"Searching Lavalink for: '{query}'")
            results = await wavelink.Playable.search(query, node=_get_gcp_node())
            log.info(f"Search returned: type={type(results).__name__}, "
                     f"len={len(results.tracks) if isinstance(results, wavelink.Playlist) else len(results) if isinstance(results, list) else 1}")
            if not results:
                log.info("No results found")
                self.fs.set_search_results(self.server_id, [])
                return

            tracks = []
            playlist_name: str | None = None
            if isinstance(results, wavelink.Playlist):
                # Playlist URL — return ALL tracks so user can pick
                source = results.tracks
                playlist_name = results.name or "Playlist"
            elif isinstance(results, list):
                # Text search — return top 10
                source = results[:10]
            else:
                source = [results]

            for track in source:
                thumb = getattr(track, "artwork", "") or ""
                video_id = track.identifier or ""
                # Fallback: construct YouTube thumbnail from video ID if artwork is missing
                if not thumb and video_id:
                    thumb = f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
                tracks.append({
                    "videoId": video_id,
                    "title": track.title,
                    "artist": track.author,
                    "url": track.uri or "",
                    "thumbnail": thumb,
                    "duration": track.length // 1000 if track.length else 0,
                })

            log.info(f"Returning {len(tracks)} search results (playlist={playlist_name!r})")
            self.fs.set_search_results(self.server_id, tracks, playlist_name=playlist_name)
        except Exception as e:
            log.error(f"Search failed for '{query}': {e}", exc_info=True)
            self.fs.set_search_results(self.server_id, [])
