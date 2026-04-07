import asyncio
import datetime
import discord
import wavelink
from discord.ext import commands
from config import IDLE_TIMEOUT_SECONDS, WEB_APP_URL
from services.session_manager import generate_session_code
from utils.embeds import now_playing_embed, session_embed, error_embed, success_embed
from services.spotify_client import is_spotify_url
from services.firestore_listener import FirestoreListener


class Playback(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs
        self.idle_tasks: dict[int, asyncio.Task] = {}
        self.history_buffer: dict[int, list] = {}  # server_id -> played tracks
        self.session_start: dict[int, datetime.datetime] = {}
        self.listeners: dict[int, FirestoreListener] = {}

    async def ensure_voice(self, ctx: commands.Context) -> wavelink.Player | None:
        if not ctx.author.voice:
            await ctx.send(embed=error_embed("You must be in a voice channel."))
            return None
        player = ctx.voice_client
        if not player:
            player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
            player.autoplay = wavelink.AutoPlayMode.disabled
            # Generate session code
            code = generate_session_code()
            self.fs.set_session_code(str(ctx.guild.id), code)
            self.fs.update_server_state(str(ctx.guild.id), {
                "voiceChannelId": str(ctx.author.voice.channel.id),
                "textChannelId": str(ctx.channel.id),
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

        results = await wavelink.Playable.search(track_data["url"])
        if not results:
            results = await wavelink.Playable.search(f"{track_data['title']} {track_data.get('artist', '')}")
        if not results:
            text_channel_id = self.fs.get_server_state(str(guild_id)).get("textChannelId")
            if text_channel_id:
                channel = self.bot.get_channel(int(text_channel_id))
                if channel:
                    await channel.send(embed=error_embed(f"Could not find: {track_data['title']}"))
            await self.play_next(player, guild_id)
            return

        playable = results[0] if isinstance(results, list) else results
        track_data["startedAt"] = datetime.datetime.now().isoformat()
        track_data["duration"] = playable.length // 1000
        self.fs.set_current_track(str(guild_id), track_data)

        # Add to history buffer
        if guild_id in self.history_buffer:
            self.history_buffer[guild_id].append({
                **track_data,
                "playedAt": datetime.datetime.now().isoformat(),
            })

        await player.play(playable)
        self.cancel_idle_timer(guild_id)

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player = payload.player
        if not player or not player.guild:
            return
        guild_id = player.guild.id
        state = self.fs.get_server_state(str(guild_id))
        if not state:
            return

        loop_mode = state.get("loopMode", "off")
        if loop_mode == "track" and state.get("currentTrack"):
            # Re-play current track
            current = state["currentTrack"]
            results = await wavelink.Playable.search(current["url"])
            if results:
                playable = results[0] if isinstance(results, list) else results
                await player.play(playable)
                return
        elif loop_mode == "queue" and state.get("currentTrack"):
            # Add current track back to end of queue
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
        player = await self.ensure_voice(ctx)
        if not player:
            return

        # Handle Spotify URLs — currently disabled (requires Spotify Premium developer account)
        if is_spotify_url(query):
            await ctx.send(embed=error_embed(
                "Spotify links are not currently supported. "
                "Please search by song name or paste a YouTube link instead."
            ))
            return

        # YouTube search via Lavalink
        results = await wavelink.Playable.search(query)
        if not results:
            await ctx.send(embed=error_embed(f"No results found for: {query}"))
            return

        playable = results[0] if isinstance(results, list) else results
        track_data = {
            "title": playable.title,
            "artist": playable.author,
            "url": playable.uri or query,
            "thumbnail": getattr(playable, "artwork", "") or "",
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
                "startedAt": datetime.datetime.now().isoformat(),
            })
            if ctx.guild.id in self.history_buffer:
                self.history_buffer[ctx.guild.id].append({
                    **track_data,
                    "playedAt": datetime.datetime.now().isoformat(),
                })
            await player.play(playable)
            await ctx.send(embed=now_playing_embed(track_data))
            self.cancel_idle_timer(ctx.guild.id)

    @commands.command(name="pause")
    async def pause(self, ctx: commands.Context):
        player = ctx.voice_client
        if player and player.playing:
            await player.pause(True)
            self.fs.update_server_state(str(ctx.guild.id), {"isPaused": True})
            await ctx.send(embed=success_embed("Paused."))

    @commands.command(name="resume", aliases=["unpause"])
    async def resume(self, ctx: commands.Context):
        player = ctx.voice_client
        if player and player.paused:
            await player.pause(False)
            self.fs.update_server_state(str(ctx.guild.id), {"isPaused": False})
            await ctx.send(embed=success_embed("Resumed."))

    @commands.command(name="skip", aliases=["s"])
    async def skip(self, ctx: commands.Context):
        player = ctx.voice_client
        if player and player.playing:
            await player.stop()
            await ctx.send(embed=success_embed("Skipped."))

    @commands.command(name="stop", aliases=["leave", "disconnect", "dc"])
    async def stop(self, ctx: commands.Context):
        player = ctx.voice_client
        if player:
            guild_id = ctx.guild.id
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
            await ctx.send(embed=success_embed("Disconnected. Session ended."))

    @commands.command(name="volume", aliases=["vol"])
    async def volume(self, ctx: commands.Context, vol: int):
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
        state = self.fs.get_server_state(str(ctx.guild.id))
        current = state.get("loopMode", "off") if state else "off"
        cycle = {"off": "track", "track": "queue", "queue": "off"}
        new_mode = cycle[current]
        self.fs.update_server_state(str(ctx.guild.id), {"loopMode": new_mode})
        labels = {"off": "Loop off", "track": "Looping current track", "queue": "Looping queue"}
        await ctx.send(embed=success_embed(labels[new_mode]))

    @commands.command(name="nowplaying", aliases=["np"])
    async def nowplaying(self, ctx: commands.Context):
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
            # Stop Firestore listener
            listener = self.listeners.pop(guild_id, None)
            if listener:
                listener.stop()
            await self.save_session_history(guild_id)
            self.fs.invalidate_session_code(str(guild_id))
            self.fs.update_server_state(str(guild_id), {
                "isPlaying": False,
                "isPaused": False,
                "voiceChannelId": None,
                "textChannelId": None,
            })
            text_channel_id = self.fs.get_server_state(str(guild_id)).get("textChannelId")
            await player.disconnect()
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
                started.isoformat(), datetime.datetime.now().isoformat()
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Playback(bot))
