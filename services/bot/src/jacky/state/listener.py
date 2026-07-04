"""Per-guild Firestore snapshot listener: the web dashboard's command channel.

The dashboard writes into the server doc (searchQuery, retriggerCommand,
seekPosition, endSessionRequested, queue appends, isPaused/volume changes);
this listener diffs consecutive snapshots and translates them into
PlayerService calls. Protocol fields are the pre-existing frontend contract.

Firestore snapshot callbacks arrive on an SDK thread — every handler hop
goes through run_coroutine_threadsafe onto the bot loop.
"""

import asyncio
import datetime
import logging
from datetime import timezone

from jacky.audio.models import is_url, to_track_data

log = logging.getLogger("jacky.listener")


class ServerDocListener:
    def __init__(self, bot, repo, service, server_id: str) -> None:
        self.bot = bot
        self.repo = repo
        self.service = service
        self.server_id = server_id
        self._unsubscribe = None
        self._last_state: dict | None = None
        self._resolving = False  # re-entry guard while we rewrite the queue

    def start(self) -> None:
        doc_ref = self.repo.db.collection("servers").document(self.server_id)
        self._unsubscribe = doc_ref.on_snapshot(self._on_snapshot)

    def stop(self) -> None:
        if self._unsubscribe:
            self._unsubscribe.unsubscribe()
            self._unsubscribe = None

    def _on_snapshot(self, doc_snapshot, changes, read_time) -> None:
        for doc in doc_snapshot:
            new_state = doc.to_dict()
            if self._last_state is None:
                self._last_state = new_state
                # A stale Exit click from while the bot was offline.
                if new_state.get("endSessionRequested"):
                    asyncio.run_coroutine_threadsafe(
                        self._handle_end_session(), self.bot.loop
                    )
                continue
            old, self._last_state = self._last_state, new_state
            asyncio.run_coroutine_threadsafe(
                self._handle_changes(old, new_state), self.bot.loop
            )

    async def _handle_changes(self, old: dict, new: dict) -> None:
        guild_id = int(self.server_id)

        new_query = new.get("searchQuery")
        if new_query and new_query != old.get("searchQuery"):
            await self._handle_search(new_query)
            return

        new_retrigger = new.get("retriggerCommand")
        if new_retrigger and new_retrigger != old.get("retriggerCommand"):
            await self._handle_retrigger(new_retrigger)
            await self.repo.update_state(self.server_id, {"retriggerCommand": None})
            return

        if new.get("endSessionRequested") and not old.get("endSessionRequested"):
            await self._handle_end_session()
            return

        queue_grew = False
        if not self._resolving:
            old_queue, new_queue = old.get("queue", []), new.get("queue", [])
            if len(new_queue) > len(old_queue):
                queue_grew = True
                await self._resolve_new_queue_items(new_queue)

        voice = self._voice()
        if not voice:
            return
        playing = self._playing_now()

        # Queue grew while idle -> auto-start.
        if queue_grew and not playing and not new.get("currentTrack"):
            await self.service.play_next(guild_id)
            return

        if old.get("volume") != new.get("volume"):
            await self.service.set_volume(guild_id, int(new.get("volume", 80)))

        if old.get("isPaused") != new.get("isPaused"):
            if not new.get("isPaused") and not playing and not new.get("currentTrack"):
                if new.get("queue"):
                    await self.service.play_next(guild_id)
                    return
            await self.service.pause(guild_id, bool(new.get("isPaused", False)))

        new_seek = new.get("seekPosition")
        if new_seek is not None and new_seek != old.get("seekPosition"):
            await self.service.seek(guild_id, int(new_seek))
            current = new.get("currentTrack")
            if current:
                new_started = (datetime.datetime.now(timezone.utc)
                               - datetime.timedelta(seconds=int(new_seek)))
                await self.repo.update_state(self.server_id, {
                    "currentTrack": {**current, "startedAt": new_started.isoformat()},
                    "seekPosition": None,
                })

        # Web skip: currentTrack nulled while isPlaying stays true.
        if (old.get("currentTrack") is not None
                and new.get("currentTrack") is None
                and new.get("isPlaying", False)):
            await self.service.skip(guild_id)

    def _voice(self):
        guild = self.bot.get_guild(int(self.server_id))
        return guild.voice_client if guild else None

    def _playing_now(self) -> bool:
        pos = self.service.positions.get(int(self.server_id)) or {}
        return bool(pos.get("connected"))

    async def _resolve_new_queue_items(self, queue: list) -> None:
        """Resolve items whose title is still a raw URL; expand playlist URLs."""
        updated = False
        new_queue: list = []
        for track in queue:
            title, url = track.get("title", ""), track.get("url", "")
            if not (is_url(title) and url):
                new_queue.append(track)
                continue
            try:
                result = await self.service.resolve(url)
            except Exception as exc:  # noqa: BLE001 — leave item unresolved
                log.error("failed to resolve queue item '%s': %s", url, exc)
                new_queue.append(track)
                continue
            requested_by = track.get("requestedBy", "Web User")
            if result.kind == "playlist" and len(result.tracks) > 1:
                new_queue.extend(to_track_data(t, requested_by) for t in result.tracks)
                updated = True
            elif result.tracks:
                new_queue.append({**track, **to_track_data(result.first, requested_by)})
                updated = True
            else:
                new_queue.append(track)
        if updated:
            self._resolving = True
            try:
                await self.repo.update_state(self.server_id, {"queue": new_queue})
            finally:
                self._resolving = False

    async def _handle_retrigger(self, retrigger: dict) -> None:
        command = retrigger.get("command", "")
        args = retrigger.get("args", "")
        guild_id = int(self.server_id)
        if not self._voice():
            return
        await self.repo.log_command(self.server_id, command, args, "Web User", "web")

        if command == "play" and args:
            try:
                result = await self.service.resolve(args)
            except Exception as exc:  # noqa: BLE001
                log.error("retrigger play resolve failed: %s", exc)
                return
            if not result.tracks:
                return
            tracks = (result.tracks if result.kind == "playlist" else [result.first])
            track_data = [to_track_data(t, "Web User") for t in tracks]
            playing = self._playing_now() or bool(
                (await self.repo.get_state(self.server_id) or {}).get("currentTrack")
            )
            if not playing:
                await self.service.start_current_track(guild_id, tracks[0], track_data[0])
                rest = track_data[1:]
            else:
                rest = track_data
            for td in rest:
                await self.repo.add_to_queue(self.server_id, td)
        elif command == "skip":
            await self.service.skip(guild_id)
        elif command == "pause":
            await self.service.pause(guild_id, True)
        elif command == "resume":
            await self.service.pause(guild_id, False)
        elif command == "loop":
            await self.service.cycle_loop_mode(guild_id)
        elif command == "volume" and args:
            try:
                await self.service.set_volume(guild_id, int(args))
            except ValueError:
                pass

    async def _handle_end_session(self) -> None:
        await self.service.teardown_session(
            int(self.server_id), extra_state={"endSessionRequested": None}
        )

    async def _handle_search(self, query: str) -> None:
        """Search via the node and write results back for the dashboard.

        FUTURE #4 semantics:
        - playlist URL      -> the URL's own video first, then the playlist
        - single-video URL  -> that video, then similar tracks (title search)
        - text              -> top 10 search results
        """
        try:
            result = await self.service.resolve(query)
            playlist_name = None
            if result.kind == "playlist":
                source = result.tracks_selected_first  # full playlist, URL's video first
                playlist_name = result.playlist_name or "Playlist"
            elif result.kind == "track" and result.first:
                source = [result.first]
                source += await self._similar_tracks(result.first)
            else:
                source = result.tracks[:10]
            tracks = []
            for t in source:
                td = to_track_data(t)
                td.pop("requestedBy", None)
                tracks.append({"videoId": (t.get("info") or {}).get("identifier", ""), **td})
            await self.repo.set_search_results(self.server_id, tracks, playlist_name)
        except Exception as exc:  # noqa: BLE001 — dashboard must not hang on a spinner
            log.error("search failed for '%s': %s", query, exc)
            await self.repo.set_search_results(self.server_id, [])

    async def _similar_tracks(self, base: dict, limit: int = 9) -> list:
        """Recommendations for a single pasted URL: a text search on its
        title+author, minus the base video itself. Best-effort."""
        info = base.get("info") or {}
        query = f"{info.get('title', '')} {info.get('author', '')}".strip()
        if not query:
            return []
        try:
            rec = await self.service.resolve(query)
        except Exception as exc:  # noqa: BLE001 — recommendations are optional
            log.debug("similar-tracks search failed: %s", exc)
            return []
        base_id = info.get("identifier")
        return [
            t for t in rec.tracks
            if (t.get("info") or {}).get("identifier") != base_id
        ][:limit]
