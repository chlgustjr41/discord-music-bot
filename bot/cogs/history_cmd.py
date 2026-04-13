import discord
from discord.ext import commands
from utils.embeds import error_embed
from config import EMBED_COLOR


class HistoryCmd(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs

    @commands.command(name="history", brief="Show recent play sessions")
    async def history(self, ctx: commands.Context):
        """Show the last 5 play sessions with their track listings."""
        sessions = self.fs.get_history(str(ctx.guild.id), limit=5)
        if not sessions:
            await ctx.send(embed=error_embed("No play history yet."))
            return
        embed = discord.Embed(title="Recent Sessions", color=EMBED_COLOR)
        for session in sessions:
            tracks = session.get("tracks", [])
            track_list = "\n".join(
                f"  {i+1}. {t['title']}" for i, t in enumerate(tracks[:5])
            )
            if len(tracks) > 5:
                track_list += f"\n  ... and {len(tracks) - 5} more"
            started = session.get("startedAt", "?")
            embed.add_field(
                name=f"Session {session['id']} ({started[:10]})",
                value=track_list or "No tracks",
                inline=False,
            )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(HistoryCmd(bot))
