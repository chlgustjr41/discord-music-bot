import asyncio
import datetime
import time
from datetime import timezone
import logging
import discord
import wavelink
from discord.ext import commands, tasks
from config import IDLE_TIMEOUT_SECONDS, EMPTY_CHANNEL_TIMEOUT_SECONDS, WEB_APP_URL
from services.session_manager import generate_session_code
from utils.embeds import now_playing_embed, session_embed, error_embed, success_embed
from services.spotify_client import is_spotify_url
from services.firestore_listener import FirestoreListener
from player import JackyPlayer

log = logging.getLogger(__name__)

GCP_NODE_ID = "gcp-primary"

# Play-verification + fail-safe tunables
PLAY_VERIFY_TIMEOUT = 10.0         # seconds to wait for player.playing to flip True
                                   # (needs to cover: voice handshake + YouTube URL fetch + buffer fill)
PLAY_FAIL_WINDOW = 60.0            # rolling window for counting play failures
PLAY_FAIL_THRESHOLD = 3            # failures within window -> full session reset
PLAY_NEXT_MAX_RETRIES = 5          # cap recursive play_next fallthroughs per call-chain


def get_gcp_node() -> wavelink.Node | None:
    """Return the GCP Lavalink node explicitly, avoiding any local nodes in the pool."""
    for node in wavelink.Pool.nodes.values():
        if node.identifier == GCP_NODE_ID and node.status == wavelink.NodeStatus.CONNECTED:
            return node
    return None


def _first_track(results) -> wavelink.Playable | None:
    """Extract the first Playable from a search result (list or Playlist)."""
    if isinstance(results, wavelink.Playlist):
        return results.tracks[0] if results.tracks else None
    if isinstance(results, list):
        return results[0] if results else None
    if isinstance(results, wavelink.Playable):
        return results
    return None


class Playback(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs
        self.idle_tasks: dict[int, asyncio.Task] = {}
        self.empty_channel_tasks: dict[int, asyncio.Task] = {}
        self.history_buffer: dict[int, list] = {}
        self.session_start: dict[int, datetime.datetime] = {}
        self.listeners: dict[int, FirestoreListener] = {}
        self._stopping: set[int] = set()   # guilds being explicitly stopped
        self._migrating: set[int] = set()  # guilds mid-failover (suppress state cleanup)
        self._silent_suspects: set[int] = set()  # guilds on first watchdog strike
        self._play_failures: dict[int, list[float]] = {}  # per-guild rolling failure timestamps
        self._resetting: set[int] = set()  # guilds currently mid-session-reset
        self._advancing: set[int] = set()  # guilds currently inside play_next (concurrency guard)
        self._silent_watchdog.start()

    def cog_unload(self):
        self._silent_watchdog.cancel()
        for task in list(self.idle_tasks.values()):
            task.cancel()
        for task in list(self.empty_channel_tasks.values()):
            task.cancel()

    def _log_cmd(self, ctx: commands.Context, args: str = ""):
        """Log a Discord command to Firestore command history."""
        self.fs.log_command(
            str(ctx.guild.id),
            ctx.command.name,
            args,
            ctx.author.display_name,
            str(ctx.author.id),
        )

    def _log_track(self, guild_id: int, track_data: dict):
        """Log a track to music history."""
        self.fs.log_music(str(guild_id), {
            "title": track_data.get("title", ""),
            "artist": track_data.get("artist", ""),
            "url": track_data.get("url", ""),
            "thumbnail": track_data.get("thumbnail", ""),
            "duration": track_data.get("duration", 0),
            "requestedBy": track_data.get("requestedBy", ""),
        })

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload):
        log.info(
            f"Lavalink node ready: {payload.node.identifier} "
            f"(resumed={payload.resumed})"
        )
        # GCP Lavalink restarted (not a resume): the new Lavalink session has NO
        # knowledge of any Discord voice connections.  Calling player.play() on the
        # stale player will silently fail — Lavalink accepts the REST request but
        # cannot forward audio because it never received VOICE_STATE_UPDATE /
        # VOICE_SERVER_UPDATE for this session.  Fix: force a full Discord voice
        # disconnect+reconnect for every GCP-node player so Discord re-sends those
        # events and Lavalink can establish the voice route.
        if not payload.resumed and payload.node.identifier == GCP_NODE_ID:
            log.warning("GCP Lavalink restarted with new session — recovering active guilds")
            for guild in self.bot.guilds:
                if guild.id in self._stopping or guild.id in self._migrating:
                    continue
                player = guild.voice_client
                if not player or not player.connected:
                    continue
                # Skip players already on a local node — unaffected by GCP restart
                player_node = getattr(player, "node", None)
                if player_node and player_node.identifier != GCP_NODE_ID:
                    continue
                state = self.fs.get_server_state(str(guild.id)) or {}
                asyncio.create_task(
                    self._recover_player_on_gcp_restart(guild.id, player, state, payload.node)
                )

    async def _recover_player_on_gcp_restart(
        self,
        guild_id: int,
        player: wavelink.Player,
        state: dict,
        node: wavelink.Node,
    ):
        """Full disconnect+reconnect for a player after a non-resumed GCP Lavalink restart.

        Calling player.play() on the stale player object silently produces no audio
        because the new Lavalink session was never given the Discord voice endpoint.
        Disconnecting from Discord voice and reconnecting causes Discord to re-send
        VOICE_STATE_UPDATE + VOICE_SERVER_UPDATE, which wavelink forwards to Lavalink.
        """
        voice_channel = player.channel
        current_track = state.get("currentTrack")

        self._migrating.add(guild_id)
        try:
            try:
                await player.disconnect()
            except Exception as e:
                log.warning(f"GCP restart recovery: disconnect failed for guild {guild_id}: {e}")

            try:
                new_player: JackyPlayer = await voice_channel.connect(cls=JackyPlayer, node=node)
                new_player.autoplay = wavelink.AutoPlayMode.disabled
            except Exception as e:
                log.error(f"GCP restart recovery: reconnect failed for guild {guild_id}: {e}")
                return

            if guild_id not in self.listeners:
                listener = FirestoreListener(self.bot, self.fs, str(guild_id))
                listener.start()
                self.listeners[guild_id] = listener

            if current_track:
                await self._resume_on_node(guild_id, new_player, current_track, node)
            else:
                # Bot was idle — reconnect is enough; future j!play will work normally
                log.info(f"GCP restart recovery: reconnected idle player for guild {guild_id}")
        except Exception as e:
            log.error(f"GCP restart recovery failed for guild {guild_id}: {e}")
        finally:
            self._migrating.discard(guild_id)

    def _get_preferred_node(self, guild_id: int) -> wavelink.Node | None:
        """Return the preferred Lavalink node for a guild, if a local one is configured."""
        localnode_cog = self.bot.cogs.get("LocalNode")
        if localnode_cog:
            return localnode_cog.get_node_for_guild(guild_id)
        return None

    async def ensure_voice(self, ctx: commands.Context) -> wavelink.Player | None:
        if not ctx.author.voice:
            await ctx.send(embed=error_embed("You must be in a voice channel."))
            return None
        player = ctx.voice_client
        if not player:
            preferred_node = self._get_preferred_node(ctx.guild.id)
            gcp_node = get_gcp_node()
            active_node = preferred_node or gcp_node
            if not active_node:
                await ctx.send(embed=error_embed(
                    "The audio server (Lavalink) is not reachable right now.\n"
                    "If you just restarted it, wait 15–20 seconds and try again.\n"
                    "You can also run `j!admin restart lavalink` to force a restart."
                ))
                return None
            try:
                connect_kwargs = {"cls": JackyPlayer, "node": active_node}
                player = await ctx.author.voice.channel.connect(**connect_kwargs)
            except Exception as e:
                log.error(f"Failed to connect to voice channel: {e}")
                await ctx.send(embed=error_embed(f"Failed to join voice channel: {e}"))
                return None
            player.autoplay = wavelink.AutoPlayMode.disabled
            # Initialize server state in Firestore
            self.fs.init_server_state(str(ctx.guild.id))
            # Generate session code
            code = generate_session_code()
            self.fs.set_session_code(str(ctx.guild.id), code)
            # Fresh session: clear queue and set server info
            self.fs.update_server_state(str(ctx.guild.id), {
                "voiceChannelId": str(ctx.author.voice.channel.id),
                "voiceChannelName": ctx.author.voice.channel.name,
                "textChannelId": str(ctx.channel.id),
                "queue": [],
                "currentTrack": None,
                "isPlaying": False,
                "isPaused": False,
                "serverName": ctx.guild.name,
                "serverIcon": str(ctx.guild.icon.url) if ctx.guild.icon else "",
            })
            await ctx.send(embed=session_embed(code, WEB_APP_URL))
            # Init history buffer
            self.history_buffer[ctx.guild.id] = []
            self.session_start[ctx.guild.id] = datetime.datetime.now()
            # Start Firestore listener for web app sync
            listener = FirestoreListener(self.bot, self.fs, str(ctx.guild.id))
            listener.start()
            self.listeners[ctx.guild.id] = listener
        return player

    async def _verify_play_started(
        self,
        player: wavelink.Player,
        timeout: float = PLAY_VERIFY_TIMEOUT,
    ) -> bool:
        """Return True iff `player.playing` flips True within `timeout` seconds.

        Lavalink accepts player.play() at the REST layer even when the Discord
        voice route is stale (common after a Lavalink session change or node
        swap).  Watching `player.playing` is the cheapest live signal that audio
        actually started flowing.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if not player.connected:
                return False
            if player.playing:
                return True
            await asyncio.sleep(0.25)
        return player.playing

    def _record_play_failure(self, guild_id: int) -> int:
        """Append now() to the guild's failure log; prune entries past the window.
        Returns the current number of failures inside the window.
        """
        now = time.monotonic()
        log_list = self._play_failures.setdefault(guild_id, [])
        log_list.append(now)
        cutoff = now - PLAY_FAIL_WINDOW
        self._play_failures[guild_id] = [t for t in log_list if t >= cutoff]
        return len(self._play_failures[guild_id])

    def _clear_play_failures(self, guild_id: int) -> None:
        self._play_failures.pop(guild_id, None)

    async def _reconnect_voice(
        self,
        guild_id: int,
        player: wavelink.Player,
    ) -> wavelink.Player | None:
        """Tear down the current voice connection and reconnect on the preferred node.

        Discord re-sends VOICE_SERVER_UPDATE / VOICE_STATE_UPDATE after a fresh
        connect, which is what Lavalink needs to route audio when its session
        has lost voice state (e.g. node restart, stale player).
        """
        if guild_id in self._resetting:
            return None
        voice_channel = player.channel
        if voice_channel is None:
            return None
        self._migrating.add(guild_id)
        try:
            try:
                await player.disconnect()
            except Exception as e:
                log.warning(f"_reconnect_voice: disconnect failed for guild {guild_id}: {e}")
            await asyncio.sleep(0.5)
            node = self._get_preferred_node(guild_id) or get_gcp_node()
            connect_kwargs = {"cls": JackyPlayer}
            if node:
                connect_kwargs["node"] = node
            try:
                new_player: JackyPlayer = await voice_channel.connect(**connect_kwargs)
            except Exception as e:
                log.error(f"_reconnect_voice: reconnect failed for guild {guild_id}: {e}")
                return None
            new_player.autoplay = wavelink.AutoPlayMode.disabled
            return new_player
        finally:
            self._migrating.discard(guild_id)

    async def reset_session(self, guild_id: int, reason: str = "repeated play failures") -> bool:
        """Full session reset: disconnect voice, clear live state, keep queue intact.

        Invoked after too many play verifications fail in a rolling window.
        Preserves the user's queue so a manual j!play / j!start resumes cleanly.
        """
        if guild_id in self._resetting:
            return False
        self._resetting.add(guild_id)
        try:
            log.warning(f"Session reset for guild {guild_id}: {reason}")
            await self._notify_text(
                guild_id,
                f"🔁 Restarting session — {reason}. Your queue is preserved.",
            )
            guild = self.bot.get_guild(guild_id)
            player = guild.voice_client if guild else None
            if player:
                self._stopping.add(guild_id)
                try:
                    await player.disconnect()
                except Exception as e:
                    log.warning(f"reset_session: disconnect failed for {guild_id}: {e}")
                finally:
                    self._stopping.discard(guild_id)
            listener = self.listeners.pop(guild_id, None)
            if listener:
                listener.stop()
            state = self.fs.get_server_state(str(guild_id)) or {}
            current = state.get("currentTrack")
            updates: dict = {
                "isPlaying": False,
                "isPaused": False,
                "currentTrack": None,
            }
            # Preserve queue; prepend the interrupted track so nothing is lost
            if current:
                queue_item = {k: v for k, v in current.items()
                              if k not in ("startedAt", "seekPosition")}
                updates["queue"] = [queue_item] + state.get("queue", [])
            self.fs.update_server_state(str(guild_id), updates)
            self._clear_play_failures(guild_id)
            self._silent_suspects.discard(guild_id)
            self._advancing.discard(guild_id)
            await self._notify_text(
                guild_id,
                "✅ Session reset complete — run `j!play <query>` to resume.",
            )
            return True
        finally:
            self._resetting.discard(guild_id)

    async def _safe_play(
        self,
        player: wavelink.Player,
        playable: wavelink.Playable,
        guild_id: int,
        *,
        start: int | None = None,
    ) -> tuple[bool, wavelink.Player]:
        """Play a track and verify audio actually started.

        If verification fails once, tear down voice and reconnect, then retry.
        If still silent, record a failure; after PLAY_FAIL_THRESHOLD failures
        inside PLAY_FAIL_WINDOW seconds, trigger a full session reset.

        Returns (success, possibly-new-player).
        """
        play_kwargs: dict = {}
        if start is not None:
            play_kwargs["start"] = start
        try:
            await player.play(playable, **play_kwargs)
        except Exception as e:
            log.error(f"_safe_play: player.play() raised for guild {guild_id}: {e}")
            fails = self._record_play_failure(guild_id)
            if fails >= PLAY_FAIL_THRESHOLD:
                asyncio.create_task(
                    self.reset_session(guild_id, "player.play() errors")
                )
            return False, player

        if await self._verify_play_started(player):
            self._clear_play_failures(guild_id)
            return True, player

        log.warning(
            f"_safe_play: no audio within {PLAY_VERIFY_TIMEOUT}s for guild {guild_id} — "
            f"reconnecting voice and retrying once"
        )
        new_player = await self._reconnect_voice(guild_id, player)
        if new_player is None:
            fails = self._record_play_failure(guild_id)
            if fails >= PLAY_FAIL_THRESHOLD:
                asyncio.create_task(
                    self.reset_session(guild_id, "voice reconnect failed")
                )
            return False, player

        try:
            await new_player.play(playable, **play_kwargs)
        except Exception as e:
            log.error(f"_safe_play: retry play() raised for guild {guild_id}: {e}")
            fails = self._record_play_failure(guild_id)
            if fails >= PLAY_FAIL_THRESHOLD:
                asyncio.create_task(
                    self.reset_session(guild_id, "retry play errors")
                )
            return False, new_player

        if await self._verify_play_started(new_player):
            self._clear_play_failures(guild_id)
            return True, new_player

        fails = self._record_play_failure(guild_id)
        log.error(
            f"_safe_play: still silent after reconnect for guild {guild_id} "
            f"(failures in window: {fails})"
        )
        if fails >= PLAY_FAIL_THRESHOLD:
            asyncio.create_task(
                self.reset_session(guild_id, "persistent silent playback")
            )
        return False, new_player

    async def play_next(
        self,
        player: wavelink.Player,
        guild_id: int,
        _depth: int = 0,
    ):
        # Per-guild concurrency guard: prevents a stale on_wavelink_track_end event
        # (arriving while _safe_play is still in its reconnect+retry phase) from
        # spawning a second play_next chain that double-pops tracks off the queue.
        if _depth == 0:
            if guild_id in self._advancing:
                log.debug(f"play_next: already advancing for guild {guild_id} — ignoring duplicate call")
                return
            self._advancing.add(guild_id)

        try:
            await self._play_next_inner(player, guild_id, _depth)
        finally:
            if _depth == 0:
                self._advancing.discard(guild_id)

    async def _play_next_inner(
        self,
        player: wavelink.Player,
        guild_id: int,
        _depth: int,
    ):
        if _depth >= PLAY_NEXT_MAX_RETRIES:
            log.error(
                f"play_next: max retry depth reached for guild {guild_id} — giving up"
            )
            self.fs.set_current_track(str(guild_id), None)
            self.fs.update_server_state(str(guild_id), {"isPlaying": False})
            await self._notify_text(
                guild_id,
                "⚠️ Skipped several unplayable tracks in a row — stopping auto-advance.",
            )
            self.start_idle_timer(guild_id, player)
            return

        track_data = self.fs.pop_next_track(str(guild_id))
        if not track_data:
            self.fs.set_current_track(str(guild_id), None)
            self.fs.update_server_state(str(guild_id), {"isPlaying": False})
            self.start_idle_timer(guild_id, player)
            return

        try:
            node = self._get_preferred_node(guild_id) or get_gcp_node()
            results = await wavelink.Playable.search(track_data["url"], node=node)
            if not results:
                results = await wavelink.Playable.search(
                    f"{track_data['title']} {track_data.get('artist', '')}", node=node
                )
        except Exception as e:
            log.error(f"Search failed in play_next: {e}")
            results = None

        if not results:
            # Count search failures toward the circuit breaker so a run of all-unresolvable
            # tracks (e.g. transient Lavalink/YouTube outage) triggers reset_session instead
            # of silently burning through the entire queue.
            fails = self._record_play_failure(guild_id)
            log.warning(f"play_next: no search results for '{track_data['title']}' in guild {guild_id} (failures: {fails})")
            if fails >= PLAY_FAIL_THRESHOLD:
                asyncio.create_task(
                    self.reset_session(guild_id, "tracks unresolvable — possible Lavalink/YouTube outage")
                )
                return
            text_channel_id = (self.fs.get_server_state(str(guild_id)) or {}).get("textChannelId")
            if text_channel_id:
                channel = self.bot.get_channel(int(text_channel_id))
                if channel:
                    await channel.send(
                        embed=error_embed(f"Could not find: {track_data['title']}")
                    )
            await self._play_next_inner(player, guild_id, _depth + 1)
            return

        playable = _first_track(results)
        if not playable:
            await self._play_next_inner(player, guild_id, _depth + 1)
            return

        # Update track_data with resolved metadata from Lavalink
        track_data["title"] = playable.title or track_data.get("title", "Unknown")
        track_data["artist"] = playable.author or track_data.get("artist", "")
        thumb = getattr(playable, "artwork", "") or ""
        if not thumb and playable.identifier:
            thumb = f"https://img.youtube.com/vi/{playable.identifier}/mqdefault.jpg"
        track_data["thumbnail"] = thumb or track_data.get("thumbnail", "")
        track_data["duration"] = playable.length // 1000
        track_data["url"] = playable.uri or track_data.get("url", "")
        track_data["startedAt"] = datetime.datetime.now(timezone.utc).isoformat()
        self.fs.set_current_track(str(guild_id), track_data)

        # Add to history buffer (initialize if missing, e.g. bot restarted mid-session)
        if guild_id not in self.history_buffer:
            self.history_buffer[guild_id] = []
            self.session_start[guild_id] = datetime.datetime.now()
        self.history_buffer[guild_id].append({
            **track_data,
            "playedAt": datetime.datetime.now(timezone.utc).isoformat(),
        })

        # Log to music history (single source of truth — only when a track actually plays)
        self._log_track(guild_id, track_data)

        ok, player = await self._safe_play(player, playable, guild_id)
        if ok:
            self.cancel_idle_timer(guild_id)
            # Announce in text channel (if Discord notifications enabled)
            server_state = self.fs.get_server_state(str(guild_id))
            if server_state.get("discordNotify", True):
                text_channel_id = server_state.get("textChannelId")
                if text_channel_id:
                    channel = self.bot.get_channel(int(text_channel_id))
                    if channel:
                        await channel.send(embed=now_playing_embed(track_data))
            return

        # _safe_play either exhausted retries or scheduled a session reset.
        self.fs.set_current_track(str(guild_id), None)
        if guild_id in self._resetting:
            # Session reset in flight — don't cascade into another play_next.
            return
        # Otherwise skip this track and try the next one (with depth guard).
        await self._play_next_inner(player, guild_id, _depth + 1)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Handle bot disconnects and empty-channel detection for human members."""
        guild_id = member.guild.id

        # ── Bot events ──────────────────────────────────────────────────────
        if member.id == self.bot.user.id:
            if before.channel is not None and after.channel is None:
                if guild_id in self._migrating:
                    return
                log.info(f"Bot disconnected from voice in guild {guild_id} (kicked/moved)")
                self._stopping.add(guild_id)
                listener = self.listeners.pop(guild_id, None)
                if listener:
                    listener.stop()
                await self.save_session_history(guild_id)
                self.fs.invalidate_session_code(str(guild_id))
                self.fs.update_server_state(str(guild_id), {
                    "isPlaying": False,
                    "isPaused": False,
                    "voiceChannelId": None,
                    "voiceChannelName": None,
                    "textChannelId": None,
                })
                self.cancel_idle_timer(guild_id)
                self.cancel_empty_channel_timer(guild_id)
                self._silent_suspects.discard(guild_id)
                self._advancing.discard(guild_id)
                self._stopping.discard(guild_id)
            return

        # ── Human member events ─────────────────────────────────────────────
        player = member.guild.voice_client
        if not player or not player.connected:
            return
        bot_channel = player.channel

        # Member left the bot's channel
        if before.channel == bot_channel and after.channel != bot_channel:
            humans = [m for m in bot_channel.members if not m.bot]
            if not humans:
                log.info(f"Voice channel empty in guild {guild_id} — starting empty-channel timer")
                self.start_empty_channel_timer(guild_id, player)

        # Member joined (or moved into) the bot's channel
        if after.channel == bot_channel and before.channel != bot_channel:
            if guild_id in self.empty_channel_tasks:
                log.info(f"User rejoined in guild {guild_id} — cancelling empty-channel timer")
                self.cancel_empty_channel_timer(guild_id)

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player = payload.player
        if not player or not player.guild:
            return
        guild_id = player.guild.id

        log.info(f"Track ended in guild {guild_id}, reason: {payload.reason}")

        # Don't auto-play if the bot is being explicitly stopped/disconnected
        if guild_id in self._stopping or guild_id in self._migrating:
            return

        reason = str(payload.reason).lower()
        if "replaced" in reason:
            return

        # "cleanup" fires when the Lavalink WebSocket dies.
        # Only trigger a hard failover for LOCAL node players — their failure is permanent.
        # For the GCP node, transient blips self-resolve; the silent watchdog catches
        # genuine outages after two consecutive strikes (~40s), avoiding the disconnect
        # churn that a hair-trigger failover causes on brief GCP hiccups.
        if "cleanup" in reason:
            if not player.connected:
                return
            player_node = getattr(player, "node", None)
            node_id = player_node.identifier if player_node else ""
            if node_id.startswith("local-") and guild_id not in self._migrating:
                asyncio.create_task(self.handle_node_failover(guild_id, notify=True))
            return

        if not player.connected:
            return

        state = self.fs.get_server_state(str(guild_id))
        if not state:
            return

        loop_mode = state.get("loopMode", "off")
        if loop_mode == "track" and state.get("currentTrack"):
            current = state["currentTrack"]
            try:
                results = await wavelink.Playable.search(current["url"], node=get_gcp_node())
                if results:
                    playable = _first_track(results)
                    if playable:
                        ok, _ = await self._safe_play(player, playable, guild_id)
                        if ok:
                            return
            except Exception as e:
                log.error(f"Failed to loop track: {e}")
        elif loop_mode == "queue" and state.get("currentTrack"):
            current = state["currentTrack"]
            self.fs.add_to_queue(str(guild_id), {
                "title": current["title"],
                "artist": current.get("artist", ""),
                "url": current["url"],
                "thumbnail": current.get("thumbnail", ""),
                "duration": current.get("duration", 0),
                "requestedBy": current.get("requestedBy", ""),
            })

        await self.play_next(player, guild_id)

    @commands.command(name="start", aliases=["join"], brief="Join voice channel and start a web session")
    async def start(self, ctx: commands.Context):
        """Join your voice channel and generate a session code for the web dashboard.

        Use this when you want to set up the web dashboard before queuing music.
        Aliases: j!join"""
        self._log_cmd(ctx)
        already_connected = ctx.voice_client is not None
        player = await self.ensure_voice(ctx)
        if not player:
            return
        # Only re-send the session code if the bot was already in voice;
        # ensure_voice already sends it when creating a new player.
        if already_connected:
            state = self.fs.get_server_state(str(ctx.guild.id))
            code = state.get("sessionCode") if state else None
            if code:
                await ctx.send(embed=session_embed(code, WEB_APP_URL))

    @commands.command(name="play", aliases=["p"], brief="Play a song or add it to the queue")
    async def play(self, ctx: commands.Context, *, query: str):
        """Play a song by name, URL, or playlist link.

        Supports YouTube, SoundCloud, and Bandcamp. If a track is already playing,
        the new track is added to the queue. Playlist URLs add all tracks at once.
        Aliases: j!p

        Examples:
          j!play never gonna give you up
          j!play https://youtube.com/watch?v=...
          j!play https://youtube.com/playlist?list=..."""
        self._log_cmd(ctx, query)
        player = await self.ensure_voice(ctx)
        if not player:
            return

        if is_spotify_url(query):
            await ctx.send(embed=error_embed(
                "Spotify links are not currently supported. "
                "Please search by song name or paste a YouTube link instead."
            ))
            return

        try:
            results = await wavelink.Playable.search(query, node=get_gcp_node())
        except Exception as e:
            log.error(f"Search failed for query '{query}': {e}")
            await ctx.send(embed=error_embed(
                f"Search failed: {e}\n\nMake sure Lavalink is running and has YouTube support enabled."
            ))
            return

        if not results:
            await ctx.send(embed=error_embed(f"No results found for: {query}"))
            return

        # Handle playlist results — add all tracks to queue
        if isinstance(results, wavelink.Playlist) and len(results.tracks) > 1:
            tracks = results.tracks
            playlist_name = results.name or "Playlist"
            all_track_data = []
            for t in tracks:
                t_thumb = getattr(t, "artwork", "") or ""
                if not t_thumb and t.identifier:
                    t_thumb = f"https://img.youtube.com/vi/{t.identifier}/mqdefault.jpg"
                all_track_data.append({
                    "title": t.title,
                    "artist": t.author,
                    "url": t.uri or query,
                    "thumbnail": t_thumb,
                    "duration": t.length // 1000 if t.length else 0,
                    "requestedBy": ctx.author.display_name,
                })

            if not player.playing:
                # Play the first track immediately, queue the rest
                first = all_track_data[0]
                self.fs.set_current_track(str(ctx.guild.id), {
                    **first,
                    "startedAt": datetime.datetime.now(timezone.utc).isoformat(),
                })
                if ctx.guild.id not in self.history_buffer:
                    self.history_buffer[ctx.guild.id] = []
                    self.session_start[ctx.guild.id] = datetime.datetime.now()
                self.history_buffer[ctx.guild.id].append({
                    **first,
                    "playedAt": datetime.datetime.now(timezone.utc).isoformat(),
                })
                self._log_track(ctx.guild.id, first)
                ok, player = await self._safe_play(player, tracks[0], ctx.guild.id)
                if ok:
                    self.fs.update_server_state(str(ctx.guild.id), {"isPlaying": True})
                    self.cancel_idle_timer(ctx.guild.id)
                else:
                    log.error(f"Failed to play first playlist track for guild {ctx.guild.id}")
                    self.fs.set_current_track(str(ctx.guild.id), None)
                rest = all_track_data[1:]
            else:
                rest = all_track_data

            for td in rest:
                self.fs.add_to_queue(str(ctx.guild.id), td)

            await ctx.send(embed=success_embed(
                f"Added **{len(all_track_data)}** tracks from **{playlist_name}** to the queue."
            ))
            return

        playable = _first_track(results)
        if not playable:
            await ctx.send(embed=error_embed(f"No results found for: {query}"))
            return
        p_thumb = getattr(playable, "artwork", "") or ""
        if not p_thumb and playable.identifier:
            p_thumb = f"https://img.youtube.com/vi/{playable.identifier}/mqdefault.jpg"
        track_data = {
            "title": playable.title,
            "artist": playable.author,
            "url": playable.uri or query,
            "thumbnail": p_thumb,
            "duration": playable.length // 1000,
            "requestedBy": ctx.author.display_name,
        }

        if player.playing:
            self.fs.add_to_queue(str(ctx.guild.id), track_data)
            await ctx.send(embed=success_embed(
                f"Added to queue: **{playable.title}** — {playable.author}"
            ))
        else:
            self.fs.set_current_track(str(ctx.guild.id), {
                **track_data,
                "startedAt": datetime.datetime.now(timezone.utc).isoformat(),
            })
            if ctx.guild.id not in self.history_buffer:
                self.history_buffer[ctx.guild.id] = []
                self.session_start[ctx.guild.id] = datetime.datetime.now()
            self.history_buffer[ctx.guild.id].append({
                **track_data,
                "playedAt": datetime.datetime.now(timezone.utc).isoformat(),
            })
            self._log_track(ctx.guild.id, track_data)
            ok, player = await self._safe_play(player, playable, ctx.guild.id)
            if ok:
                self.fs.update_server_state(str(ctx.guild.id), {"isPlaying": True})
                await ctx.send(embed=now_playing_embed(track_data))
                self.cancel_idle_timer(ctx.guild.id)
            else:
                self.fs.set_current_track(str(ctx.guild.id), None)
                self.fs.update_server_state(str(ctx.guild.id), {"isPlaying": False})
                await ctx.send(embed=error_embed(
                    "Failed to start audio stream. "
                    "If this keeps happening, the session will auto-reset."
                ))

    @commands.command(name="pause", brief="Pause the current track")
    async def pause(self, ctx: commands.Context):
        """Pause the currently playing track. Use j!resume to continue."""
        self._log_cmd(ctx)
        player = ctx.voice_client
        if player and player.playing:
            await player.pause(True)
            self.fs.update_server_state(str(ctx.guild.id), {"isPaused": True})
            await ctx.send(embed=success_embed("Paused."))

    @commands.command(name="resume", aliases=["unpause"], brief="Resume paused playback")
    async def resume(self, ctx: commands.Context):
        """Resume playback after pausing. Aliases: j!unpause"""
        self._log_cmd(ctx)
        player = ctx.voice_client
        if player and player.paused:
            await player.pause(False)
            self.fs.update_server_state(str(ctx.guild.id), {"isPaused": False})
            await ctx.send(embed=success_embed("Resumed."))

    @commands.command(name="skip", aliases=["s"], brief="Skip to the next track")
    async def skip(self, ctx: commands.Context):
        """Skip the current track and play the next one in the queue. Aliases: j!s"""
        self._log_cmd(ctx)
        player = ctx.voice_client
        if player and player.playing:
            await player.stop()
            await ctx.send(embed=success_embed("Skipped."))

    @commands.command(name="stop", aliases=["leave", "disconnect", "dc"], brief="Stop playback and leave voice")
    async def stop(self, ctx: commands.Context):
        """Stop all playback, clear the queue, and disconnect from voice.

        This ends the current session and invalidates the web dashboard session code.
        Aliases: j!leave, j!disconnect, j!dc"""
        self._log_cmd(ctx)
        player = ctx.voice_client
        if player:
            guild_id = ctx.guild.id
            # Mark as stopping so on_wavelink_track_end doesn't auto-play
            self._stopping.add(guild_id)
            # Stop Firestore listener
            listener = self.listeners.pop(guild_id, None)
            if listener:
                listener.stop()
            # Save history
            await self.save_session_history(guild_id)
            # Clean up
            self.fs.clear_queue(str(guild_id))
            self.fs.invalidate_session_code(str(guild_id))
            self.fs.update_server_state(str(guild_id), {
                "isPlaying": False,
                "isPaused": False,
                "voiceChannelId": None,
                "textChannelId": None,
            })
            self.cancel_idle_timer(guild_id)
            self.cancel_empty_channel_timer(guild_id)
            await player.disconnect()
            self._stopping.discard(guild_id)
            await ctx.send(embed=success_embed("Disconnected. Session ended."))

    @commands.command(name="reset", brief="Reset the voice session (recovers silent playback)")
    async def reset(self, ctx: commands.Context):
        """Tear down and rebuild the voice session without losing your queue.

        Use this if the bot is in the channel but you hear no audio. The queue
        is preserved; use j!play or j!start to resume."""
        self._log_cmd(ctx)
        await self.reset_session(ctx.guild.id, reason=f"manual reset by {ctx.author.display_name}")

    @commands.command(name="volume", aliases=["vol"], brief="Set volume (0-100)")
    async def volume(self, ctx: commands.Context, vol: int):
        """Set the playback volume (0-100). Aliases: j!vol

        Example: j!volume 50"""
        self._log_cmd(ctx, str(vol))
        player = ctx.voice_client
        if not player:
            await ctx.send(embed=error_embed("Not connected to voice."))
            return
        vol = max(0, min(100, vol))
        await player.set_volume(vol)
        self.fs.update_server_state(str(ctx.guild.id), {"volume": vol})
        await ctx.send(embed=success_embed(f"Volume set to **{vol}%**"))

    @commands.command(name="loop", brief="Cycle loop mode: off → track → queue")
    async def loop(self, ctx: commands.Context):
        """Cycle through loop modes: off → track → queue → off.

        - off: no looping
        - track: repeat the current track
        - queue: repeat the entire queue"""
        self._log_cmd(ctx)
        state = self.fs.get_server_state(str(ctx.guild.id))
        current = state.get("loopMode", "off") if state else "off"
        cycle = {"off": "track", "track": "queue", "queue": "off"}
        new_mode = cycle[current]
        self.fs.update_server_state(str(ctx.guild.id), {"loopMode": new_mode})
        labels = {"off": "Loop off", "track": "Looping current track", "queue": "Looping queue"}
        await ctx.send(embed=success_embed(labels[new_mode]))

    @commands.command(name="nowplaying", aliases=["np"], brief="Show the current track")
    async def nowplaying(self, ctx: commands.Context):
        """Show details about the currently playing track. Aliases: j!np"""
        self._log_cmd(ctx)
        state = self.fs.get_server_state(str(ctx.guild.id))
        if state and state.get("currentTrack"):
            await ctx.send(embed=now_playing_embed(state["currentTrack"]))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."))

    # ------------------------------------------------------------------
    #  Node migration — move active player to a specific node
    # ------------------------------------------------------------------

    async def migrate_player_to_node(self, guild_id: int, target_node: wavelink.Node):
        """Disconnect the active player and reconnect it on target_node.

        Called by LocalNode after a successful local node connect so that
        audio actually routes through the local Lavalink, not just the pool.
        No-op if already on the target node or no player is active.
        """
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        player = guild.voice_client
        if not player or not player.connected:
            return

        if getattr(player, "node", None) is target_node:
            return

        state = self.fs.get_server_state(str(guild_id))
        current_track = state.get("currentTrack") if state else None
        voice_channel = player.channel
        text_channel_id = state.get("textChannelId") if state else None

        self._migrating.add(guild_id)
        try:
            try:
                await player.disconnect()
            except Exception as e:
                log.warning(f"Disconnect during migration failed for guild {guild_id}: {e}")

            try:
                new_player: JackyPlayer = await voice_channel.connect(cls=JackyPlayer, node=target_node)
                new_player.autoplay = wavelink.AutoPlayMode.disabled
            except Exception as e:
                log.error(f"Reconnect during migration failed for guild {guild_id}: {e}")
                if text_channel_id:
                    ch = self.bot.get_channel(int(text_channel_id))
                    if ch:
                        await ch.send(embed=error_embed(
                            "Failed to migrate session to local node. Staying on cloud audio."
                        ))
                return

            if guild_id not in self.listeners:
                listener = FirestoreListener(self.bot, self.fs, str(guild_id))
                listener.start()
                self.listeners[guild_id] = listener

            if current_track:
                await self._resume_on_node(guild_id, new_player, current_track, target_node)
                if text_channel_id:
                    ch = self.bot.get_channel(int(text_channel_id))
                    if ch:
                        await ch.send(embed=success_embed(
                            "🎵 Session migrated to **local node** — resuming from where you left off."
                        ))
            else:
                await self.play_next(new_player, guild_id)
        finally:
            self._migrating.discard(guild_id)

    # ------------------------------------------------------------------
    #  Node failover — called by LocalNode cog when local node dies
    # ------------------------------------------------------------------

    async def handle_node_failover(self, guild_id: int, notify: bool = True):
        """Migrate active playback from a dead local node to the GCP node.

        Called by the LocalNode health check loop AFTER the dead node has
        been ejected from the pool so Pool.get_best_node() returns only GCP.
        """
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        player = guild.voice_client
        if not player or not player.connected:
            return

        state = self.fs.get_server_state(str(guild_id))
        current_track = state.get("currentTrack") if state else None
        voice_channel_id = state.get("voiceChannelId") if state else None

        gcp_node = get_gcp_node()
        if not gcp_node:
            log.error(f"GCP node unavailable during failover for guild {guild_id}")
            return

        if notify:
            await self._notify_text(
                guild_id,
                "⚡ Local audio node disconnected — switching to cloud.\n"
                "Run `j!localnode reconnect` when your local node is back online.",
            )

        # Guard against voice_state_update cleaning up session state
        self._migrating.add(guild_id)
        try:
            try:
                await player.disconnect()
            except Exception as e:
                log.warning(f"Disconnect during failover failed for guild {guild_id}: {e}")

            if not voice_channel_id:
                return

            channel = self.bot.get_channel(int(voice_channel_id))
            if not channel:
                return

            try:
                new_player: JackyPlayer = await channel.connect(cls=JackyPlayer, node=gcp_node)
                new_player.autoplay = wavelink.AutoPlayMode.disabled
            except Exception as e:
                log.error(f"Reconnect failed during failover for guild {guild_id}: {e}")
                return

            # Re-register Firestore listener if needed
            if guild_id not in self.listeners:
                listener = FirestoreListener(self.bot, self.fs, str(guild_id))
                listener.start()
                self.listeners[guild_id] = listener

            if current_track:
                await self._resume_on_node(guild_id, new_player, current_track, gcp_node)
            else:
                # Nothing was playing — start the queue if there is one
                await self.play_next(new_player, guild_id)

        finally:
            self._migrating.discard(guild_id)

    async def _resume_on_node(
        self,
        guild_id: int,
        player: wavelink.Player,
        track_data: dict,
        node: wavelink.Node,
    ):
        """Attempt to resume a track from approximately the right position."""
        try:
            results = await asyncio.wait_for(
                wavelink.Playable.search(track_data["url"], node=node),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            log.warning(f"Resume search timed out for guild {guild_id}")
            await self.play_next(player, guild_id)
            return
        except Exception as e:
            log.error(f"Resume search failed for guild {guild_id}: {e}")
            await self.play_next(player, guild_id)
            return

        playable = _first_track(results)
        if not playable:
            await self.play_next(player, guild_id)
            return

        start_ms = 0
        started_at = track_data.get("startedAt")
        if started_at:
            try:
                dt = datetime.datetime.fromisoformat(started_at)
                elapsed = (datetime.datetime.now(timezone.utc) - dt).total_seconds()
                start_ms = int(max(0, elapsed) * 1000)
            except Exception:
                pass

        ok, _ = await self._safe_play(player, playable, guild_id, start=start_ms)
        if ok:
            self.cancel_idle_timer(guild_id)
            log.info(f"Resumed '{track_data.get('title')}' at {start_ms}ms for guild {guild_id}")
        else:
            log.error(f"Resume play failed for guild {guild_id}")
            if guild_id not in self._resetting:
                await self.play_next(player, guild_id)

    async def _notify_text(self, guild_id: int, message: str):
        """Post a message to the guild's last text channel."""
        try:
            state = self.fs.get_server_state(str(guild_id))
            ch_id = state.get("textChannelId") if state else None
            if not ch_id:
                return
            ch = self.bot.get_channel(int(ch_id))
            if ch:
                await ch.send(embed=success_embed(message))
        except Exception:
            pass

    # --- Silent player watchdog ---
    # Two-strike detection: a guild must appear stuck for TWO consecutive 20-second
    # ticks (~40s total) before recovery is triggered.  This eliminates false positives
    # during normal track transitions, which last 1–10s and clear on the next tick.

    @tasks.loop(seconds=20)
    async def _silent_watchdog(self):
        for guild in self.bot.guilds:
            try:
                player = guild.voice_client
                if not player or not player.connected:
                    self._silent_suspects.discard(guild.id)
                    continue
                if guild.id in self._stopping or guild.id in self._migrating:
                    self._silent_suspects.discard(guild.id)
                    continue
                if player.playing or getattr(player, "paused", False):
                    self._silent_suspects.discard(guild.id)
                    continue
                state = self.fs.get_server_state(str(guild.id))
                if not state or not state.get("isPlaying") or not state.get("currentTrack") \
                        or state.get("isPaused"):
                    self._silent_suspects.discard(guild.id)
                    continue

                # First strike — mark and wait for next tick
                if guild.id not in self._silent_suspects:
                    self._silent_suspects.add(guild.id)
                    log.debug(f"Silent watchdog: first strike for guild {guild.id}")
                    continue

                # Second consecutive strike — genuinely stuck, recover
                self._silent_suspects.discard(guild.id)
                log.warning(
                    f"Silent watchdog: two consecutive strikes for guild {guild.id} — recovering"
                )
                await self._notify_text(
                    guild.id,
                    "⚠️ Audio stream interrupted — recovering session...",
                )
                asyncio.create_task(self.handle_node_failover(guild.id, notify=False))
            except Exception as e:
                log.error(f"Silent watchdog failed for guild {guild.id}: {e}")

    @_silent_watchdog.before_loop
    async def _watchdog_wait_ready(self):
        await self.bot.wait_until_ready()

    # --- Idle Timer ---

    def start_idle_timer(self, guild_id: int, player: wavelink.Player):
        self.cancel_idle_timer(guild_id)
        self.idle_tasks[guild_id] = asyncio.create_task(
            self._idle_disconnect(guild_id, player)
        )

    def cancel_idle_timer(self, guild_id: int):
        task = self.idle_tasks.pop(guild_id, None)
        if task:
            task.cancel()

    async def _idle_disconnect(self, guild_id: int, player: wavelink.Player):
        await asyncio.sleep(IDLE_TIMEOUT_SECONDS)
        if not player.connected or player.playing:
            return
        self._stopping.add(guild_id)
        listener = self.listeners.pop(guild_id, None)
        if listener:
            listener.stop()
        await self.save_session_history(guild_id)
        self.fs.invalidate_session_code(str(guild_id))
        state = self.fs.get_server_state(str(guild_id)) or {}
        text_channel_id = state.get("textChannelId")
        self.fs.update_server_state(str(guild_id), {
            "isPlaying": False,
            "isPaused": False,
            "currentTrack": None,
            "voiceChannelId": None,
            "voiceChannelName": None,
            "textChannelId": None,
        })
        self.cancel_empty_channel_timer(guild_id)
        await player.disconnect()
        self._stopping.discard(guild_id)
        if text_channel_id:
            ch = self.bot.get_channel(int(text_channel_id))
            if ch:
                await ch.send(embed=success_embed(
                    f"Disconnected — no tracks queued for "
                    f"{IDLE_TIMEOUT_SECONDS // 60} minutes. Session ended."
                ))

    # --- Empty-channel Timer ---

    def start_empty_channel_timer(self, guild_id: int, player: wavelink.Player):
        self.cancel_empty_channel_timer(guild_id)
        self.empty_channel_tasks[guild_id] = asyncio.create_task(
            self._empty_channel_disconnect(guild_id, player)
        )

    def cancel_empty_channel_timer(self, guild_id: int):
        task = self.empty_channel_tasks.pop(guild_id, None)
        if task:
            task.cancel()

    async def _empty_channel_disconnect(self, guild_id: int, player: wavelink.Player):
        await asyncio.sleep(EMPTY_CHANNEL_TIMEOUT_SECONDS)
        if not player.connected:
            return
        # Re-check: someone may have rejoined during the sleep
        if player.channel:
            humans = [m for m in player.channel.members if not m.bot]
            if humans:
                return
        self._stopping.add(guild_id)
        listener = self.listeners.pop(guild_id, None)
        if listener:
            listener.stop()
        await self.save_session_history(guild_id)
        self.fs.invalidate_session_code(str(guild_id))
        state = self.fs.get_server_state(str(guild_id)) or {}
        text_channel_id = state.get("textChannelId")
        # Preserve queue; move current track back so it resumes at the start next time
        current = state.get("currentTrack")
        updates: dict = {
            "isPlaying": False,
            "isPaused": False,
            "currentTrack": None,
            "voiceChannelId": None,
            "voiceChannelName": None,
            "textChannelId": None,
        }
        if current:
            queue_item = {k: v for k, v in current.items()
                          if k not in ("startedAt", "seekPosition")}
            updates["queue"] = [queue_item] + state.get("queue", [])
        self.fs.update_server_state(str(guild_id), updates)
        self.cancel_idle_timer(guild_id)
        await player.disconnect()
        self._stopping.discard(guild_id)
        if text_channel_id:
            ch = self.bot.get_channel(int(text_channel_id))
            if ch:
                await ch.send(embed=success_embed(
                    f"Disconnected — voice channel was empty for "
                    f"{EMPTY_CHANNEL_TIMEOUT_SECONDS // 60} minutes.\n"
                    "Queue saved. Use `j!play` or `j!start` to resume."
                ))

    async def save_session_history(self, guild_id: int):
        tracks = self.history_buffer.pop(guild_id, [])
        started = self.session_start.pop(guild_id, None)
        if tracks and started:
            session_id = started.strftime("%Y%m%d-%H%M%S")
            self.fs.save_history(
                str(guild_id), session_id, tracks,
                started.isoformat(), datetime.datetime.now(timezone.utc).isoformat()
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Playback(bot))
