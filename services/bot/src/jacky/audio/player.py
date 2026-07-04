"""Playback orchestration: queue advancement, session lifecycle, recovery.

Design boundaries (spec §3.1):
- Firestore first, then Lavalink — containers hold only caches (ADR-0003).
- NO watchdog code lives here. The guardian owns failure detection; this
  service only reacts to events it receives (track end, node ready) and to
  explicit user/dashboard commands.
- Recovery is convergence, not choreography: on bot start or on a
  non-resumed Lavalink session, state is rebuilt from Firestore and the
  cached voice payload, then playback is re-issued at the estimated position.
"""

import asyncio
import datetime
import logging
import time
from datetime import timezone
from typing import Any

from jacky.audio.models import LoadResult, to_identifier, to_track_data
from jacky.audio.node import NodeError
from jacky.audio.provider import NodeProvider
from jacky.state.repository import ServerRepository, generate_session_code

log = logging.getLogger("jacky.player")

PLAY_NEXT_MAX_RETRIES = 5      # unplayable-track fallthroughs per chain
FAIL_WINDOW_SECONDS = 60.0     # rolling window for the failure breaker
FAIL_THRESHOLD = 3             # failures in window -> stop auto-advance
SEARCH_ATTEMPTS = 3
SEARCH_RETRY_DELAY = 2.0


class PlayerService:
    def __init__(
        self,
        bot: Any,
        provider: NodeProvider,
        repo: ServerRepository,
        settings: Any,
        notifier: Any,
        listener_factory: Any = None,
    ) -> None:
        self.bot = bot
        self.provider = provider
        self.repo = repo
        self.settings = settings
        self.notifier = notifier
        self.listener_factory = listener_factory

        self.listeners: dict[int, Any] = {}
        self.history_buffer: dict[int, list] = {}
        self.session_start: dict[int, datetime.datetime] = {}
        self.positions: dict[int, dict] = {}  # guild -> last playerUpdate state (cache)
        self.idle_tasks: dict[int, asyncio.Task] = {}
        self.empty_channel_tasks: dict[int, asyncio.Task] = {}
        self._stopping: set[int] = set()
        self._advancing: set[int] = set()
        self._failures: dict[int, list[float]] = {}
        self.search_retry_delay = SEARCH_RETRY_DELAY  # tests dial this to 0

    # ── helpers ──────────────────────────────────────────────────────────

    def _voice(self, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        return guild.voice_client if guild else None

    def _record_failure(self, guild_id: int) -> int:
        now = time.monotonic()
        entries = self._failures.setdefault(guild_id, [])
        entries.append(now)
        self._failures[guild_id] = [t for t in entries if t >= now - FAIL_WINDOW_SECONDS]
        return len(self._failures[guild_id])

    def _clear_failures(self, guild_id: int) -> None:
        self._failures.pop(guild_id, None)

    async def resolve(self, query: str) -> LoadResult:
        node = self.provider.node_for(0)
        return await node.load_tracks(to_identifier(query))

    async def _resolve_track_data(self, guild_id: int, td: dict) -> dict | None:
        """URL first, then title+artist text search; 3 attempts with backoff."""
        node = self.provider.node_for(guild_id)
        for attempt in range(SEARCH_ATTEMPTS):
            if attempt:
                await asyncio.sleep(self.search_retry_delay)
            try:
                result = await node.load_tracks(to_identifier(td.get("url") or td["title"]))
                if not result.tracks:
                    query = f"{td['title']} {td.get('artist', '')}".strip()
                    result = await node.load_tracks(to_identifier(query))
                if result.tracks:
                    return result.first
            except Exception as exc:  # noqa: BLE001 — retried, then counted by caller
                log.warning(
                    "resolve attempt %d/%d for '%s' in guild %s: %s",
                    attempt + 1, SEARCH_ATTEMPTS, td.get("title"), guild_id, exc,
                )
        return None

    async def _issue_play(
        self, guild_id: int, encoded: str, *, position_ms: int = 0
    ) -> bool:
        node = self.provider.node_for(guild_id)
        state = await self.repo.get_state(str(guild_id)) or {}
        payload: dict = {
            "track": {"encoded": encoded},
            "position": position_ms,
            "volume": int(state.get("volume", 80)),
            "paused": False,
        }
        try:
            await node.update_player(guild_id, payload)
            return True
        except NodeError as exc:
            log.error("play rejected for guild %s: %s", guild_id, exc)
            return False

    # ── session lifecycle ────────────────────────────────────────────────

    async def begin_session(self, guild: Any, voice_channel: Any, text_channel: Any) -> str:
        sid = str(guild.id)
        await self.repo.init_state(sid)
        code = generate_session_code()
        await self.repo.set_session_code(sid, code)
        await self._set_nickname(guild.id, code)
        await self.repo.update_state(sid, {
            "voiceChannelId": str(voice_channel.id),
            "voiceChannelName": voice_channel.name,
            "textChannelId": str(text_channel.id),
            "queue": [],
            "currentTrack": None,
            "isPlaying": False,
            "isPaused": False,
            "serverName": guild.name,
            "serverIcon": str(guild.icon.url) if guild.icon else "",
        })
        self.history_buffer[guild.id] = []
        self.session_start[guild.id] = datetime.datetime.now(timezone.utc)
        self.start_listener(guild.id)
        return code

    def start_listener(self, guild_id: int) -> None:
        if self.listener_factory and guild_id not in self.listeners:
            listener = self.listener_factory(guild_id)
            listener.start()
            self.listeners[guild_id] = listener

    def stop_listener(self, guild_id: int) -> None:
        listener = self.listeners.pop(guild_id, None)
        if listener:
            try:
                listener.stop()
            except Exception as exc:  # noqa: BLE001 — teardown must not abort teardown
                log.warning("listener stop failed for guild %s: %s", guild_id, exc)

    async def teardown_session(
        self,
        guild_id: int,
        *,
        requeue_current: bool = False,
        clear_queue: bool = False,
        disconnect: bool = True,
        extra_state: dict | None = None,
        message: str | None = None,
    ) -> None:
        """The one exit path: every way a session ends funnels through here."""
        sid = str(guild_id)
        self._stopping.add(guild_id)
        try:
            self.stop_listener(guild_id)
            await self.save_session_history(guild_id)
            await self.repo.invalidate_session_code(sid)
            state = await self.repo.get_state(sid) or {}
            updates: dict = {
                "isPlaying": False,
                "isPaused": False,
                "currentTrack": None,
                "voiceChannelId": None,
                "voiceChannelName": None,
                "textChannelId": None,
            }
            if clear_queue:
                updates["queue"] = []
            elif requeue_current and state.get("currentTrack"):
                current = state["currentTrack"]
                queue_item = {k: v for k, v in current.items()
                              if k not in ("startedAt", "seekPosition")}
                updates["queue"] = [queue_item] + state.get("queue", [])
            if extra_state:
                updates.update(extra_state)
            await self.repo.update_state(sid, updates)
            self.cancel_idle_timer(guild_id)
            self.cancel_empty_channel_timer(guild_id)
            self.positions.pop(guild_id, None)
            self._clear_failures(guild_id)
            if disconnect:
                voice = self._voice(guild_id)
                if voice:
                    try:
                        await voice.disconnect(force=True)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("disconnect failed for guild %s: %s", guild_id, exc)
            await self._set_nickname(guild_id, None)
            if message:
                await self.notifier.send(guild_id, text=message,
                                         text_channel_id=state.get("textChannelId"))
        finally:
            self._stopping.discard(guild_id)

    async def _set_nickname(self, guild_id: int, code: str | None) -> None:
        guild = self.bot.get_guild(guild_id)
        if not guild or not getattr(guild, "me", None):
            return
        try:
            await guild.me.edit(nick=f"Jacky Music · {code}" if code else None)
        except Exception as exc:  # noqa: BLE001 — nickname is a nice-to-have
            log.debug("nickname update failed for guild %s: %s", guild_id, exc)

    async def save_session_history(self, guild_id: int) -> None:
        tracks = self.history_buffer.pop(guild_id, [])
        started = self.session_start.pop(guild_id, None)
        if tracks and started:
            session_id = started.strftime("%Y%m%d-%H%M%S")
            await self.repo.save_history(
                str(guild_id), session_id, tracks,
                started.isoformat(), datetime.datetime.now(timezone.utc).isoformat(),
            )

    # ── playback ─────────────────────────────────────────────────────────

    async def start_current_track(self, guild_id: int, track: dict, td: dict) -> bool:
        """Persist td as currentTrack (Firestore FIRST), then instruct Lavalink."""
        sid = str(guild_id)
        td = dict(td)
        td["startedAt"] = datetime.datetime.now(timezone.utc).isoformat()
        await self.repo.set_current_track(sid, td)
        self._remember_played(guild_id, td)
        await self.repo.log_music(sid, {k: td.get(k, "") for k in
                                        ("title", "artist", "url", "thumbnail",
                                         "duration", "requestedBy")})
        ok = await self._issue_play(guild_id, track["encoded"])
        if ok:
            await self.repo.update_state(sid, {"isPlaying": True})
            self.cancel_idle_timer(guild_id)
            self._clear_failures(guild_id)
        else:
            await self.repo.set_current_track(sid, None)
        return ok

    def _remember_played(self, guild_id: int, td: dict) -> None:
        if guild_id not in self.history_buffer:
            self.history_buffer[guild_id] = []
            self.session_start[guild_id] = datetime.datetime.now(timezone.utc)
        self.history_buffer[guild_id].append(
            {**td, "playedAt": datetime.datetime.now(timezone.utc).isoformat()}
        )

    async def play_next(self, guild_id: int, _depth: int = 0) -> None:
        # Concurrency guard: a stale TrackEnd arriving mid-advance must not
        # spawn a second chain that double-pops the queue.
        if _depth == 0:
            if guild_id in self._advancing:
                return
            self._advancing.add(guild_id)
        try:
            await self._play_next_inner(guild_id, _depth)
        finally:
            if _depth == 0:
                self._advancing.discard(guild_id)

    async def _play_next_inner(self, guild_id: int, _depth: int) -> None:
        sid = str(guild_id)
        if _depth >= PLAY_NEXT_MAX_RETRIES:
            await self.repo.set_current_track(sid, None)
            await self.repo.update_state(sid, {"isPlaying": False})
            await self.notifier.send(
                guild_id,
                text="⚠️ Skipped several unplayable tracks in a row — stopping auto-advance.",
                error=True,
            )
            self.start_idle_timer(guild_id)
            return

        td = await self.repo.pop_next_track(sid)
        if not td:
            await self.repo.set_current_track(sid, None)
            await self.repo.update_state(sid, {"isPlaying": False})
            self.start_idle_timer(guild_id)
            return

        track = await self._resolve_track_data(guild_id, td)
        if track is None:
            fails = self._record_failure(guild_id)
            if fails >= FAIL_THRESHOLD:
                # Likely a source outage (F1/F2/F3): put the track back and
                # halt instead of burning the queue. The guardian's canary
                # sees the same failure and classifies/alerts it.
                queue = await self.repo.get_queue(sid)
                await self.repo.update_state(sid, {
                    "queue": [td] + queue, "isPlaying": False, "currentTrack": None,
                })
                await self.notifier.send(
                    guild_id,
                    text="⚠️ Tracks are failing to load — pausing auto-advance. "
                         "Your queue is saved; try `j!play` in a minute.",
                    error=True,
                )
                return
            await self.notifier.send(
                guild_id, text=f"Could not find: {td.get('title', '?')}", error=True
            )
            await self._play_next_inner(guild_id, _depth + 1)
            return

        td = {**td, **to_track_data(track, td.get("requestedBy", ""))}
        ok = await self.start_current_track(guild_id, track, td)
        if ok:
            state = await self.repo.get_state(sid) or {}
            if state.get("discordNotify", True):
                await self.notifier.send(guild_id, track=td)
            return

        self._record_failure(guild_id)
        await self._play_next_inner(guild_id, _depth + 1)

    async def skip(self, guild_id: int) -> None:
        node = self.provider.node_for(guild_id)
        # Stopping emits TrackEndEvent(stopped) -> on_track_end advances.
        await node.update_player(guild_id, {"track": {"encoded": None}})

    async def pause(self, guild_id: int, paused: bool) -> None:
        node = self.provider.node_for(guild_id)
        await node.update_player(guild_id, {"paused": paused})
        await self.repo.update_state(str(guild_id), {"isPaused": paused})

    async def set_volume(self, guild_id: int, volume: int) -> int:
        volume = max(0, min(100, volume))
        node = self.provider.node_for(guild_id)
        await node.update_player(guild_id, {"volume": volume})
        await self.repo.update_state(str(guild_id), {"volume": volume})
        return volume

    async def seek(self, guild_id: int, seconds: int) -> None:
        node = self.provider.node_for(guild_id)
        await node.update_player(guild_id, {"position": int(seconds) * 1000})

    async def cycle_loop_mode(self, guild_id: int) -> str:
        sid = str(guild_id)
        state = await self.repo.get_state(sid) or {}
        cycle = {"off": "track", "track": "queue", "queue": "off"}
        new_mode = cycle.get(state.get("loopMode", "off"), "track")
        await self.repo.update_state(sid, {"loopMode": new_mode})
        return new_mode

    # ── node events ──────────────────────────────────────────────────────

    async def on_track_start(self, guild_id: int, track: dict) -> None:
        self.cancel_idle_timer(guild_id)

    async def on_track_end(self, guild_id: int, reason: str) -> None:
        if guild_id in self._stopping:
            return
        reason = reason.lower()
        if reason in ("replaced", "cleanup"):
            return
        sid = str(guild_id)
        state = await self.repo.get_state(sid)
        if not state:
            return

        loop_mode = state.get("loopMode", "off")
        current = state.get("currentTrack")
        if loop_mode == "track" and current and reason == "finished":
            track = await self._resolve_track_data(guild_id, current)
            if track and await self.start_current_track(guild_id, track, current):
                return
        elif loop_mode == "queue" and current:
            await self.repo.add_to_queue(sid, {
                k: current.get(k, "") for k in
                ("title", "artist", "url", "thumbnail", "duration", "requestedBy")
            })
        await self.play_next(guild_id)

    async def on_track_exception(self, guild_id: int, payload: dict) -> None:
        message = ((payload.get("exception") or {}).get("message")
                   or payload.get("type", "unknown"))
        log.warning("track exception in guild %s: %s", guild_id, message)
        if payload.get("type") == "TrackStuckEvent":
            # Stuck tracks emit no TrackEndEvent on their own — force one.
            try:
                await self.skip(guild_id)
            except NodeError as exc:
                log.error("stuck-track skip failed for guild %s: %s", guild_id, exc)

    async def on_player_update(self, guild_id: int, state: dict) -> None:
        self.positions[guild_id] = state

    async def on_node_ready(self, resumed: bool) -> None:
        if resumed:
            log.info("Lavalink session resumed — players intact")
            return
        log.warning("Lavalink session is NEW — re-priming voice and playback")
        for guild in list(self.bot.guilds):
            voice = getattr(guild, "voice_client", None)
            if voice is None:
                continue
            asyncio.get_running_loop().create_task(self._reprime_guild(guild.id, voice))

    async def _reprime_guild(self, guild_id: int, voice: Any) -> None:
        """After a non-resumed Lavalink restart: cached voice payload + replay."""
        try:
            if not await voice.resend_voice():
                log.warning("no cached voice payload for guild %s; skipping", guild_id)
                return
            state = await self.repo.get_state(str(guild_id)) or {}
            current = state.get("currentTrack")
            if current and state.get("isPlaying"):
                await self.resume_track(guild_id, current, paused=state.get("isPaused", False))
        except Exception as exc:  # noqa: BLE001 — per-guild recovery must not cascade
            log.error("re-prime failed for guild %s: %s", guild_id, exc)

    async def resume_track(self, guild_id: int, td: dict, *, paused: bool = False) -> bool:
        """Play td at the position estimated from its startedAt timestamp."""
        track = await self._resolve_track_data(guild_id, td)
        if track is None:
            await self.play_next(guild_id)
            return False
        position_ms = 0
        started_at = td.get("startedAt")
        if started_at:
            try:
                dt = datetime.datetime.fromisoformat(started_at)
                elapsed = (datetime.datetime.now(timezone.utc) - dt).total_seconds()
                position_ms = int(max(0.0, elapsed) * 1000)
            except ValueError:
                pass
        ok = await self._issue_play(guild_id, track["encoded"], position_ms=position_ms)
        if ok and paused:
            try:
                await self.pause(guild_id, True)
            except NodeError:
                pass
        if ok:
            log.info("resumed '%s' at %dms for guild %s", td.get("title"), position_ms, guild_id)
        else:
            await self.play_next(guild_id)
        return ok

    # ── startup convergence (crash-only recovery) ────────────────────────

    async def converge_on_startup(self) -> None:
        """Rebuild every live session from Firestore after a bot restart."""
        try:
            server_ids = await self.repo.active_server_ids()
        except Exception as exc:  # noqa: BLE001 — startup must not crash-loop on F8
            log.error("startup convergence: cannot list servers: %s", exc)
            return
        for sid in server_ids:
            try:
                await self._converge_server(sid)
            except Exception as exc:  # noqa: BLE001
                log.error("startup convergence failed for %s: %s", sid, exc)

    async def _converge_server(self, sid: str) -> None:
        from jacky.audio.voice import LavalinkVoiceClient

        guild_id = int(sid)
        state = await self.repo.get_state(sid) or {}
        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(int(state["voiceChannelId"])) if guild else None
        humans = [m for m in channel.members if not m.bot] if channel else []
        if not channel or not humans:
            log.info("convergence: session for %s is stale (channel gone/empty) — clearing", sid)
            await self.teardown_session(guild_id, requeue_current=True, disconnect=False)
            return
        log.info("convergence: re-joining %s in guild %s", channel.name, sid)
        await channel.connect(cls=LavalinkVoiceClient)
        self.start_listener(guild_id)
        await self._set_nickname(guild_id, state.get("sessionCode"))
        current = state.get("currentTrack")
        if current:
            await self.resume_track(guild_id, current, paused=state.get("isPaused", False))
        elif state.get("queue"):
            await self.play_next(guild_id)
        else:
            self.start_idle_timer(guild_id)

    # ── timers ───────────────────────────────────────────────────────────

    def start_idle_timer(self, guild_id: int) -> None:
        self.cancel_idle_timer(guild_id)
        self.idle_tasks[guild_id] = asyncio.get_running_loop().create_task(
            self._idle_disconnect(guild_id)
        )

    def cancel_idle_timer(self, guild_id: int) -> None:
        task = self.idle_tasks.pop(guild_id, None)
        if task:
            task.cancel()

    async def _idle_disconnect(self, guild_id: int) -> None:
        await asyncio.sleep(self.settings.idle_timeout_seconds)
        voice = self._voice(guild_id)
        state = await self.repo.get_state(str(guild_id)) or {}
        if not voice or state.get("isPlaying"):
            return
        minutes = self.settings.idle_timeout_seconds // 60
        await self.teardown_session(
            guild_id,
            message=f"Disconnected — no tracks queued for {minutes} minutes. Session ended.",
        )

    def start_empty_channel_timer(self, guild_id: int) -> None:
        self.cancel_empty_channel_timer(guild_id)
        self.empty_channel_tasks[guild_id] = asyncio.get_running_loop().create_task(
            self._empty_channel_disconnect(guild_id)
        )

    def cancel_empty_channel_timer(self, guild_id: int) -> None:
        task = self.empty_channel_tasks.pop(guild_id, None)
        if task:
            task.cancel()

    async def _empty_channel_disconnect(self, guild_id: int) -> None:
        await asyncio.sleep(self.settings.empty_channel_timeout_seconds)
        voice = self._voice(guild_id)
        if not voice:
            return
        channel = getattr(voice, "channel", None)
        if channel and any(not m.bot for m in channel.members):
            return  # someone came back during the countdown
        minutes = self.settings.empty_channel_timeout_seconds // 60
        await self.teardown_session(
            guild_id,
            requeue_current=True,
            message=(f"Disconnected — voice channel was empty for {minutes} minutes.\n"
                     "Queue saved. Use `j!play` or `j!start` to resume."),
        )
