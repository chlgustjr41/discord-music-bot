import discord
from discord.ext import commands
from config import WEB_APP_URL
from utils.embeds import error_embed


class Activation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs  # FirestoreClient set on bot in main.py

    async def cog_check(self, ctx: commands.Context) -> bool:
        """Global check — runs before every command in every cog."""
        return True  # This cog's own commands don't need the check

    @commands.Cog.listener()
    async def on_command(self, ctx: commands.Context):
        pass  # Logging hook if needed later


class ActivationCheck(commands.Cog):
    """Registers a bot-wide check for server activation."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs
        bot.add_check(self.global_activation_check)

    async def global_activation_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            await ctx.send(embed=error_embed("Commands only work in a server."))
            return False
        if not self.fs.is_server_activated(str(ctx.guild.id)):
            await ctx.send(embed=error_embed(
                f"This server has not been activated.\n"
                f"The server owner must visit [{WEB_APP_URL}]({WEB_APP_URL}) "
                f"and sign in with Google to activate Jacky Music."
            ))
            return False
        return True

    def cog_unload(self):
        self.bot.remove_check(self.global_activation_check)


async def setup(bot: commands.Bot):
    await bot.add_cog(ActivationCheck(bot))
