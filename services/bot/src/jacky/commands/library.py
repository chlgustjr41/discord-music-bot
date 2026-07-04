"""Playlist + history commands (ports of the legacy playlist_cmd/history_cmd)."""

import discord
from discord.ext import commands

from jacky.commands.embeds import EMBED_COLOR, error_embed, success_embed


class Library(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.repo = bot.repo

    @commands.group(
        name="playlist", aliases=["pl"], invoke_without_command=True,
        brief="Manage saved playlists",
    )
    async def playlist(self, ctx: commands.Context) -> None:
        await ctx.send(embed=error_embed(
            "Usage: `j!playlist save <name>`, `j!playlist load <name>`, "
            "`j!playlist list`, `j!playlist delete <name>`"
        ))

    @playlist.command(name="save", brief="Save the current queue as a playlist")
    async def save(self, ctx: commands.Context, *, name: str) -> None:
        state = await self.repo.get_state(str(ctx.guild.id))
        if not state:
            await ctx.send(embed=error_embed("No active session."))
            return
        keys = ("title", "artist", "url", "thumbnail", "duration")
        tracks = []
        if state.get("currentTrack"):
            tracks.append({k: state["currentTrack"].get(k, "") for k in keys})
        tracks.extend({k: t.get(k, "") for k in keys} for t in state.get("queue", []))
        if not tracks:
            await ctx.send(embed=error_embed("Nothing to save — queue is empty."))
            return
        await self.repo.save_playlist(str(ctx.guild.id), name, tracks, ctx.author.display_name)
        await ctx.send(embed=success_embed(f"Saved playlist **{name}** with {len(tracks)} tracks."))

    @playlist.command(name="load", brief="Load a playlist into the queue")
    async def load(self, ctx: commands.Context, *, name: str) -> None:
        playlist_data = await self.repo.load_playlist(str(ctx.guild.id), name)
        if not playlist_data:
            await ctx.send(embed=error_embed(f"Playlist **{name}** not found."))
            return
        tracks = playlist_data.get("tracks", [])
        for track in tracks:
            await self.repo.add_to_queue(
                str(ctx.guild.id), {**track, "requestedBy": ctx.author.display_name}
            )
        await ctx.send(embed=success_embed(
            f"Loaded **{len(tracks)}** tracks from playlist **{name}** into queue."
        ))

    @playlist.command(name="list", aliases=["ls"], brief="List all saved playlists")
    async def list_playlists(self, ctx: commands.Context) -> None:
        playlists = await self.repo.list_playlists(str(ctx.guild.id))
        if not playlists:
            await ctx.send(embed=error_embed("No saved playlists."))
            return
        embed = discord.Embed(title="Saved Playlists", color=EMBED_COLOR)
        embed.description = "\n".join(
            f"**{p['name']}** — {len(p.get('tracks', []))} tracks (by {p.get('createdBy', '?')})"
            for p in playlists
        )
        await ctx.send(embed=embed)

    @playlist.command(name="delete", aliases=["del", "rm"], brief="Delete a saved playlist")
    async def delete(self, ctx: commands.Context, *, name: str) -> None:
        if not await self.repo.load_playlist(str(ctx.guild.id), name):
            await ctx.send(embed=error_embed(f"Playlist **{name}** not found."))
            return
        await self.repo.delete_playlist(str(ctx.guild.id), name)
        await ctx.send(embed=success_embed(f"Deleted playlist **{name}**."))

    @commands.command(name="history", brief="Show recent play sessions")
    async def history(self, ctx: commands.Context) -> None:
        sessions = await self.repo.get_history(str(ctx.guild.id), limit=5)
        if not sessions:
            await ctx.send(embed=error_embed("No play history yet."))
            return
        embed = discord.Embed(title="Recent Sessions", color=EMBED_COLOR)
        for session in sessions:
            tracks = session.get("tracks", [])
            track_list = "\n".join(
                f"  {i + 1}. {t['title']}" for i, t in enumerate(tracks[:5])
            )
            if len(tracks) > 5:
                track_list += f"\n  ... and {len(tracks) - 5} more"
            started = session.get("startedAt", "?")
            embed.add_field(
                name=f"Session {session['id']} ({str(started)[:10]})",
                value=track_list or "No tracks",
                inline=False,
            )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Library(bot))
