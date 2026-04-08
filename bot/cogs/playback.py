import asyncio
import datetime
from datetime import timezone
import logging
import discord
import wavelink
from discord.ext import commands
from config import IDLE_TIMEOUT_SECONDS, WEB_APP_URL
from services.session_manager import generate_session_code
from utils.embeds import now_playing_embed, session_embed, error_embed, success_embed
from services.spotify_client import is_spotify_url
from services.firestore_listener import FirestoreListener
from player import JackyPlayer

log = logging.getLogger(__name__)


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
        self.history_buffer: dict[int, list] = {}
        self.session_start: dict[int, datetime.datetime] = {}
        self.listeners: dict[int, FirestoreListener] = {}
        self._stopping: set[int] = set()  # guilds currently being stopped

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
        log.info(f"Lavalink node connected: {payload.node.identifier}")

    async def ensure_voice(self, ctx: commands.Context) -> wavelink.Player | None:
        if not ctx.author.voice:
            await ctx.send(embed=error_embed("You must be in a voice channel."))
            return None
        player = ctx.voice_client
        if not player:
            try:
                player = await ctx.author.voice.channel.connect(cls=JackyPlayer)
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

    async def play_next(self, player: wavelink.Player, guild_id: int):
        track_data = self.fs.pop_next_track(str(guild_id))
        if not track_data:
            self.fs.set_current_track(str(guild_id), None)
            self.fs.update_server_state(str(guild_id), {"isPlaying": False})
            self.start_idle_timer(guild_id, player)
            return

        try:
            results = await wavelink.Playable.search(track_data["url"])
            if not results:
                results = await wavelink.Playable.search(
                    f"{track_data['title']} {track_data.get('artist', '')}"
                )
        except Exception as e:
            log.error(f"Search failed in play_next: {e}")
            results = None

        if not results:
            text_channel_id = self.fs.get_server_state(str(guild_id)).get("textChannelId")
            if text_channel_id:
                channel = self.bot.get_channel(int(text_channel_id))
                if channel:
                    await channel.send(
                        embed=error_embed(f"Could not find: {track_data['title']}")
                    )
            await self.play_next(player, guild_id)
            return

        playable = _first_track(results)
        if not playable:
            await self.play_next(player, guild_id)
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

        try:
            await player.play(playable)
            self.cancel_idle_timer(guild_id)

            # Announce in text channel
            text_channel_id = self.fs.get_server_state(str(guild_id)).get("textChannelId")
            if text_channel_id:
                channel = self.bot.get_channel(int(text_channel_id))
                if channel:
                    await channel.send(embed=now_playing_embed(track_data))
        except Exception as e:
            log.error(f"Failed to play track in play_next: {e}")
            self.fs.set_current_track(str(guild_id), None)
            self.start_idle_timer(guild_id, player)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Detect when the bot is kicked/moved out of a voice channel."""
        if member.id != self.bot.user.id:
            return
        # Bot was in a channel and is now disconnected
        if before.channel is not None and after.channel is None:
            guild_id = member.guild.id
            log.info(f"Bot disconnected from voice in guild {guild_id} (kicked/moved)")
            self._stopping.add(guild_id)
            # Stop Firestore listener
            listener = self.listeners.pop(guild_id, None)
            if listener:
                listener.stop()
            # Save history
            await self.save_session_history(guild_id)
            # Update Firestore state
            self.fs.invalidate_session_code(str(guild_id))
            self.fs.update_server_state(str(guild_id), {
                "isPlaying": False,
                "isPaused": False,
                "voiceChannelId": None,
                "voiceChannelName": None,
                "textChannelId": None,
            })
            self.cancel_idle_timer(guild_id)
            self._stopping.discard(guild_id)

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player = payload.player
        if not player or not player.guild:
            return
        guild_id = player.guild.id

        log.info(f"Track ended in guild {guild_id}, reason: {payload.reason}")

        # Don't auto-play if the bot is being explicitly stopped/disconnected
        if guild_id in self._stopping:
            return

        # "cleanup" = player destroyed (disconnect), "replaced" = another track started
        reason = str(payload.reason).lower()
        if "cleanup" in reason or "replaced" in reason:
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
                results = await wavelink.Playable.search(current["url"])
                if results:
                    playable = _first_track(results)
                    if playable:
                        await player.play(playable)
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

    @commands.command(name="play", aliases=["p"])
    async def play(self, ctx: commands.Context, *, query: str):
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
            results = await wavelink.Playable.search(query)
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
                try:
                    await player.play(tracks[0])
                    self.fs.update_server_state(str(ctx.guild.id), {"isPlaying": True})
                    self.cancel_idle_timer(ctx.guild.id)
                except Exception as e:
                    log.error(f"Failed to play first playlist track: {e}")
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
            try:
                await player.play(playable)
                self.fs.update_server_state(str(ctx.guild.id), {"isPlaying": True})
                await ctx.send(embed=now_playing_embed(track_data))
                self.cancel_idle_timer(ctx.guild.id)
            except Exception as e:
                log.error(f"Failed to play track: {e}")
                self.fs.set_current_track(str(ctx.guild.id), None)
                self.fs.update_server_state(str(ctx.guild.id), {"isPlaying": False})
                await ctx.send(embed=error_embed(
                    f"Failed to play: {e}\n\nCheck Lavalink logs for details."
                ))

    @commands.command(name="pause")
    async def pause(self, ctx: commands.Context):
        self._log_cmd(ctx)
        player = ctx.voice_client
        if player and player.playing:
            await player.pause(True)
            self.fs.update_server_state(str(ctx.guild.id), {"isPaused": True})
            await ctx.send(embed=success_embed("Paused."))

    @commands.command(name="resume", aliases=["unpause"])
    async def resume(self, ctx: commands.Context):
        self._log_cmd(ctx)
        player = ctx.voice_client
        if player and player.paused:
            await player.pause(False)
            self.fs.update_server_state(str(ctx.guild.id), {"isPaused": False})
            await ctx.send(embed=success_embed("Resumed."))

    @commands.command(name="skip", aliases=["s"])
    async def skip(self, ctx: commands.Context):
        self._log_cmd(ctx)
        player = ctx.voice_client
        if player and player.playing:
            await player.stop()
            await ctx.send(embed=success_embed("Skipped."))

    @commands.command(name="stop", aliases=["leave", "disconnect", "dc"])
    async def stop(self, ctx: commands.Context):
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
            await player.disconnect()
            self._stopping.discard(guild_id)
            await ctx.send(embed=success_embed("Disconnected. Session ended."))

    @commands.command(name="volume", aliases=["vol"])
    async def volume(self, ctx: commands.Context, vol: int):
        self._log_cmd(ctx, str(vol))
        player = ctx.voice_client
        if not player:
            await ctx.send(embed=error_embed("Not connected to voice."))
            return
        vol = max(0, min(100, vol))
        await player.set_volume(vol)
        self.fs.update_server_state(str(ctx.guild.id), {"volume": vol})
        await ctx.send(embed=success_embed(f"Volume set to **{vol}%**"))

    @commands.command(name="loop")
    async def loop(self, ctx: commands.Context):
        self._log_cmd(ctx)
        state = self.fs.get_server_state(str(ctx.guild.id))
        current = state.get("loopMode", "off") if state else "off"
        cycle = {"off": "track", "track": "queue", "queue": "off"}
        new_mode = cycle[current]
        self.fs.update_server_state(str(ctx.guild.id), {"loopMode": new_mode})
        labels = {"off": "Loop off", "track": "Looping current track", "queue": "Looping queue"}
        await ctx.send(embed=success_embed(labels[new_mode]))

    @commands.command(name="nowplaying", aliases=["np"])
    async def nowplaying(self, ctx: commands.Context):
        self._log_cmd(ctx)
        state = self.fs.get_server_state(str(ctx.guild.id))
        if state and state.get("currentTrack"):
            await ctx.send(embed=now_playing_embed(state["currentTrack"]))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."))

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
        if player.connected and not player.playing:
            self._stopping.add(guild_id)
            listener = self.listeners.pop(guild_id, None)
            if listener:
                listener.stop()
            await self.save_session_history(guild_id)
            self.fs.invalidate_session_code(str(guild_id))
            text_channel_id = self.fs.get_server_state(str(guild_id)).get("textChannelId")
            self.fs.update_server_state(str(guild_id), {
                "isPlaying": False,
                "isPaused": False,
                "voiceChannelId": None,
                "textChannelId": None,
            })
            await player.disconnect()
            self._stopping.discard(guild_id)
            if text_channel_id:
                channel = self.bot.get_channel(int(text_channel_id))
                if channel:
                    await channel.send(embed=success_embed(
                        "Disconnected due to inactivity. Session ended."
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
