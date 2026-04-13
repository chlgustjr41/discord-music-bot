from discord.ext import commands
from utils.embeds import error_embed, success_embed
from config import EMBED_COLOR
import discord


class PlaylistCmd(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs

    @commands.group(name="playlist", aliases=["pl"], invoke_without_command=True, brief="Manage saved playlists")
    async def playlist(self, ctx: commands.Context):
        """Save, load, list, or delete playlists. Aliases: j!pl

        Subcommands: save, load, list, delete"""
        await ctx.send(embed=error_embed(
            "Usage: `j!playlist save <name>`, `j!playlist load <name>`, "
            "`j!playlist list`, `j!playlist delete <name>`"
        ))

    @playlist.command(name="save", brief="Save the current queue as a playlist")
    async def save(self, ctx: commands.Context, *, name: str):
        """Save the current track and queue as a named playlist.

        Example: j!playlist save chill vibes"""
        state = self.fs.get_server_state(str(ctx.guild.id))
        if not state:
            await ctx.send(embed=error_embed("No active session."))
            return
        queue = state.get("queue", [])
        current = state.get("currentTrack")
        tracks = []
        if current:
            tracks.append({
                "title": current["title"],
                "artist": current.get("artist", ""),
                "url": current["url"],
                "thumbnail": current.get("thumbnail", ""),
                "duration": current.get("duration", 0),
            })
        tracks.extend([{
            "title": t["title"],
            "artist": t.get("artist", ""),
            "url": t["url"],
            "thumbnail": t.get("thumbnail", ""),
            "duration": t.get("duration", 0),
        } for t in queue])
        if not tracks:
            await ctx.send(embed=error_embed("Nothing to save — queue is empty."))
            return
        self.fs.save_playlist(str(ctx.guild.id), name, tracks, ctx.author.display_name)
        await ctx.send(embed=success_embed(f"Saved playlist **{name}** with {len(tracks)} tracks."))

    @playlist.command(name="load", brief="Load a playlist into the queue")
    async def load(self, ctx: commands.Context, *, name: str):
        """Load a saved playlist's tracks into the current queue.

        Example: j!playlist load chill vibes"""
        playlist_data = self.fs.load_playlist(str(ctx.guild.id), name)
        if not playlist_data:
            await ctx.send(embed=error_embed(f"Playlist **{name}** not found."))
            return
        tracks = playlist_data.get("tracks", [])
        for track in tracks:
            track["requestedBy"] = ctx.author.display_name
            self.fs.add_to_queue(str(ctx.guild.id), track)
        await ctx.send(embed=success_embed(
            f"Loaded **{len(tracks)}** tracks from playlist **{name}** into queue."
        ))

    @playlist.command(name="list", aliases=["ls"], brief="List all saved playlists")
    async def list_playlists(self, ctx: commands.Context):
        """Show all saved playlists for this server. Aliases: j!playlist ls"""
        playlists = self.fs.list_playlists(str(ctx.guild.id))
        if not playlists:
            await ctx.send(embed=error_embed("No saved playlists."))
            return
        embed = discord.Embed(title="Saved Playlists", color=EMBED_COLOR)
        lines = []
        for p in playlists:
            count = len(p.get("tracks", []))
            lines.append(f"**{p['name']}** — {count} tracks (by {p.get('createdBy', '?')})")
        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    @playlist.command(name="delete", aliases=["del", "rm"], brief="Delete a saved playlist")
    async def delete(self, ctx: commands.Context, *, name: str):
        """Delete a saved playlist by name. Aliases: j!playlist del, j!playlist rm

        Example: j!playlist delete chill vibes"""
        existing = self.fs.load_playlist(str(ctx.guild.id), name)
        if not existing:
            await ctx.send(embed=error_embed(f"Playlist **{name}** not found."))
            return
        self.fs.delete_playlist(str(ctx.guild.id), name)
        await ctx.send(embed=success_embed(f"Deleted playlist **{name}**."))


async def setup(bot: commands.Bot):
    await bot.add_cog(PlaylistCmd(bot))
