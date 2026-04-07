from discord.ext import commands
from utils.embeds import error_embed, success_embed
from config import EMBED_COLOR
import discord


class PlaylistCmd(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs

    @commands.group(name="playlist", aliases=["pl"], invoke_without_command=True)
    async def playlist(self, ctx: commands.Context):
        await ctx.send(embed=error_embed(
            "Usage: `j!playlist save <name>`, `j!playlist load <name>`, "
            "`j!playlist list`, `j!playlist delete <name>`"
        ))

    @playlist.command(name="save")
    async def save(self, ctx: commands.Context, *, name: str):
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

    @playlist.command(name="load")
    async def load(self, ctx: commands.Context, *, name: str):
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

    @playlist.command(name="list", aliases=["ls"])
    async def list_playlists(self, ctx: commands.Context):
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

    @playlist.command(name="delete", aliases=["del", "rm"])
    async def delete(self, ctx: commands.Context, *, name: str):
        existing = self.fs.load_playlist(str(ctx.guild.id), name)
        if not existing:
            await ctx.send(embed=error_embed(f"Playlist **{name}** not found."))
            return
        self.fs.delete_playlist(str(ctx.guild.id), name)
        await ctx.send(embed=success_embed(f"Deleted playlist **{name}**."))


async def setup(bot: commands.Bot):
    await bot.add_cog(PlaylistCmd(bot))
