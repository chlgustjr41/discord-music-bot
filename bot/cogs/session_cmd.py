from discord.ext import commands
from utils.embeds import session_embed, error_embed
from config import WEB_APP_URL


class SessionCmd(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs

    @commands.command(name="session")
    async def session(self, ctx: commands.Context):
        state = self.fs.get_server_state(str(ctx.guild.id))
        if not state or not state.get("sessionCode"):
            await ctx.send(embed=error_embed(
                "No active session. Use `j!play` to start one."
            ))
            return
        await ctx.send(embed=session_embed(state["sessionCode"], WEB_APP_URL))


async def setup(bot: commands.Bot):
    await bot.add_cog(SessionCmd(bot))
