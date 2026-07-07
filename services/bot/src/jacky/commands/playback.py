"""Playback commands. Thin: Discord I/O only; all orchestration lives in
PlayerService (jacky/audio/player.py). Command surface is 1:1 with the
legacy bot so the M5 cutover is invisible to users."""

import logging

import discord
from discord.ext import commands

from jacky.audio.models import is_url, to_track_data
from jacky.audio.voice import LavalinkVoiceClient
from jacky.commands.embeds import (
    error_embed,
    now_playing_embed,
    queue_embed,
    session_embed,
    success_embed,
)

log = logging.getLogger("jacky.commands")


class Playback(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.service = bot.service
        self.repo = bot.repo
        self.settings = bot.settings

    async def _log_cmd(self, ctx: commands.Context, args: str = "") -> None:
        await self.repo.log_command(
            str(ctx.guild.id), ctx.command.name, args,
            ctx.author.display_name, str(ctx.author.id),
        )

    async def ensure_voice(self, ctx: commands.Context) -> LavalinkVoiceClient | None:
        if not ctx.author.voice:
            await ctx.send(embed=error_embed("You must be in a voice channel."))
            return None
        voice = ctx.voice_client
        if voice:
            return voice
        node = self.bot.node_provider.node_for(ctx.guild.id)
        if not node.connected:
            await ctx.send(embed=error_embed(
                "The audio server (Lavalink) is not reachable right now.\n"
                "If it just restarted, wait 15–20 seconds and try again."
            ))
            return None
        try:
            voice = await ctx.author.voice.channel.connect(cls=LavalinkVoiceClient)
        except Exception as exc:  # noqa: BLE001 — surfaced to the user
            log.error("voice connect failed in guild %s: %s", ctx.guild.id, exc)
            await ctx.send(embed=error_embed(f"Failed to join voice channel: {exc}"))
            return None
        code = await self.service.begin_session(
            ctx.guild, ctx.author.voice.channel, ctx.channel
        )
        await ctx.send(embed=session_embed(code, self.settings.web_app_url))
        return voice

    # ── voice-state housekeeping ─────────────────────────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member,
        before: discord.VoiceState, after: discord.VoiceState,
    ) -> None:
        guild_id = member.guild.id
        if member.id == self.bot.user.id:
            if before.channel is not None and after.channel is None:
                if guild_id in self.service._stopping or guild_id in self.service._resetting:
                    return  # teardown/reset already owns this exit
                log.info("bot removed from voice in guild %s (kicked/moved)", guild_id)
                await self.service.teardown_session(
                    guild_id, requeue_current=True, disconnect=False
                )
            return
        voice = member.guild.voice_client
        if not voice or not getattr(voice, "channel", None):
            return
        bot_channel = voice.channel
        if before.channel == bot_channel and after.channel != bot_channel:
            if not any(not m.bot for m in bot_channel.members):
                self.service.start_empty_channel_timer(guild_id)
        if after.channel == bot_channel and before.channel != bot_channel:
            self.service.cancel_empty_channel_timer(guild_id)

    # ── commands ─────────────────────────────────────────────────────────

    @commands.command(name="start", aliases=["join"], brief="Join voice and start a web session")
    async def start(self, ctx: commands.Context) -> None:
        """Join your voice channel and generate a web dashboard session code."""
        await self._log_cmd(ctx)
        already_connected = ctx.voice_client is not None
        if not await self.ensure_voice(ctx):
            return
        if already_connected:
            state = await self.repo.get_state(str(ctx.guild.id)) or {}
            if state.get("sessionCode"):
                await ctx.send(embed=session_embed(
                    state["sessionCode"], self.settings.web_app_url
                ))

    @commands.command(name="play", aliases=["p"], brief="Play a song or add it to the queue")
    async def play(self, ctx: commands.Context, *, query: str) -> None:
        """Play by name, URL, or playlist link (YouTube/SoundCloud/Bandcamp)."""
        await self._log_cmd(ctx, query)
        if not await self.ensure_voice(ctx):
            return
        if is_url(query) and "spotify.com" in query:
            await ctx.send(embed=error_embed(
                "Spotify links are not currently supported. "
                "Search by song name or paste a YouTube link instead."
            ))
            return
        try:
            result = await self.service.resolve(query)
        except Exception as exc:  # noqa: BLE001 — surfaced to the user
            log.error("search failed for '%s': %s", query, exc)
            await ctx.send(embed=error_embed(f"Search failed: {exc}"))
            return
        if not result.tracks:
            await ctx.send(embed=error_embed(f"No results found for: {query}"))
            return

        guild_id = ctx.guild.id
        sid = str(guild_id)
        state = await self.repo.get_state(sid) or {}
        busy = bool(state.get("currentTrack"))
        requested_by = ctx.author.display_name

        if result.kind == "playlist" and len(result.tracks) > 1:
            all_td = [to_track_data(t, requested_by) for t in result.tracks]
            rest = all_td
            if not busy:
                ok = await self.service.start_current_track(
                    guild_id, result.tracks[0], all_td[0]
                )
                rest = all_td[1:] if ok else all_td
            for td in rest:
                await self.repo.add_to_queue(sid, td)
            await ctx.send(embed=success_embed(
                f"Added **{len(all_td)}** tracks from "
                f"**{result.playlist_name or 'Playlist'}** to the queue."
            ))
            return

        td = to_track_data(result.first, requested_by)
        if busy:
            await self.repo.add_to_queue(sid, td)
            await ctx.send(embed=success_embed(
                f"Added to queue: **{td['title']}** — {td['artist']}"
            ))
        else:
            ok = await self.service.start_current_track(guild_id, result.first, td)
            if ok:
                await ctx.send(embed=now_playing_embed(td))
            else:
                await ctx.send(embed=error_embed(
                    "Failed to start the audio stream — try again in a moment."
                ))

    @commands.command(name="pause", brief="Pause the current track")
    async def pause(self, ctx: commands.Context) -> None:
        await self._log_cmd(ctx)
        await self.service.pause(ctx.guild.id, True)
        await ctx.send(embed=success_embed("Paused."))

    @commands.command(name="resume", aliases=["unpause"], brief="Resume paused playback")
    async def resume(self, ctx: commands.Context) -> None:
        await self._log_cmd(ctx)
        await self.service.pause(ctx.guild.id, False)
        await ctx.send(embed=success_embed("Resumed."))

    @commands.command(name="skip", aliases=["s"], brief="Skip to the next track")
    async def skip(self, ctx: commands.Context) -> None:
        await self._log_cmd(ctx)
        await self.service.skip(ctx.guild.id)
        await ctx.send(embed=success_embed("Skipped."))

    @commands.command(
        name="stop", aliases=["leave", "disconnect", "dc"],
        brief="Stop playback and leave voice",
    )
    async def stop(self, ctx: commands.Context) -> None:
        """Stop playback, clear the queue, disconnect, and end the web session."""
        await self._log_cmd(ctx)
        if ctx.voice_client:
            await self.service.teardown_session(ctx.guild.id, clear_queue=True)
            await ctx.send(embed=success_embed("Disconnected. Session ended."))

    @commands.command(name="reset", brief="Reset the voice session (recovers silent playback)")
    async def reset(self, ctx: commands.Context) -> None:
        """Rebuild the voice session in place: rejoin the channel and resume
        the queue. The web session and queue are preserved."""
        await self._log_cmd(ctx)
        ok = await self.service.reset_session(
            ctx.guild.id, reason=f"j!reset by {ctx.author.display_name}"
        )
        if not ok:
            await ctx.send(embed=error_embed(
                "Nothing to reset — no active voice session. Use `j!start`."
            ))

    @commands.command(name="volume", aliases=["vol"], brief="Set volume (0-100)")
    async def volume(self, ctx: commands.Context, vol: int) -> None:
        await self._log_cmd(ctx, str(vol))
        if not ctx.voice_client:
            await ctx.send(embed=error_embed("Not connected to voice."))
            return
        actual = await self.service.set_volume(ctx.guild.id, vol)
        await ctx.send(embed=success_embed(f"Volume set to **{actual}%**"))

    @commands.command(name="loop", brief="Cycle loop mode: off → track → queue")
    async def loop(self, ctx: commands.Context) -> None:
        await self._log_cmd(ctx)
        new_mode = await self.service.cycle_loop_mode(ctx.guild.id)
        labels = {"off": "Loop off", "track": "Looping current track", "queue": "Looping queue"}
        await ctx.send(embed=success_embed(labels[new_mode]))

    @commands.command(name="nowplaying", aliases=["np"], brief="Show the current track")
    async def nowplaying(self, ctx: commands.Context) -> None:
        await self._log_cmd(ctx)
        state = await self.repo.get_state(str(ctx.guild.id))
        if state and state.get("currentTrack"):
            await ctx.send(embed=now_playing_embed(state["currentTrack"]))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."))

    @commands.command(name="queue", aliases=["q"], brief="Show the current queue")
    async def queue(self, ctx: commands.Context, page: int = 1) -> None:
        state = await self.repo.get_state(str(ctx.guild.id))
        if not state:
            await ctx.send(embed=error_embed("No active session."))
            return
        await ctx.send(embed=queue_embed(
            state.get("queue", []), state.get("currentTrack"), page=page - 1
        ))

    @commands.command(name="remove", brief="Remove a track by position")
    async def remove(self, ctx: commands.Context, position: int) -> None:
        removed = await self.repo.remove_from_queue(str(ctx.guild.id), position - 1)
        if removed is None:
            queue = await self.repo.get_queue(str(ctx.guild.id))
            await ctx.send(embed=error_embed(
                f"Invalid position. Queue has {len(queue)} tracks."
            ))
            return
        await ctx.send(embed=success_embed(f"Removed: **{removed['title']}**"))

    @commands.command(name="move", brief="Move a track to a new position")
    async def move(self, ctx: commands.Context, from_pos: int, to_pos: int) -> None:
        queue = await self.repo.get_queue(str(ctx.guild.id))
        ok = await self.repo.reorder_queue(str(ctx.guild.id), from_pos - 1, to_pos - 1)
        if not ok:
            await ctx.send(embed=error_embed(
                f"Invalid positions. Queue has {len(queue)} tracks."
            ))
            return
        await ctx.send(embed=success_embed(
            f"Moved **{queue[from_pos - 1]['title']}** from position {from_pos} to {to_pos}"
        ))

    @commands.command(name="shuffle", brief="Shuffle the queue")
    async def shuffle(self, ctx: commands.Context) -> None:
        count = await self.repo.shuffle_queue(str(ctx.guild.id))
        if not count:
            await ctx.send(embed=error_embed("Queue is empty."))
            return
        await ctx.send(embed=success_embed(f"Shuffled {count} tracks."))

    @commands.command(name="session", brief="Show the web dashboard session code")
    async def session(self, ctx: commands.Context) -> None:
        state = await self.repo.get_state(str(ctx.guild.id))
        if not state or not state.get("sessionCode"):
            await ctx.send(embed=error_embed("No active session. Use `j!play` to start one."))
            return
        await ctx.send(embed=session_embed(state["sessionCode"], self.settings.web_app_url))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Playback(bot))
