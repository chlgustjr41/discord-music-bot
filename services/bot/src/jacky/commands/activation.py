"""Bot-wide server-activation gate (web signup drives `serverOwners.isActive`)."""

from discord.ext import commands

from jacky.commands.embeds import error_embed


class ActivationCheck(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.repo = bot.repo
        self.settings = bot.settings
        bot.add_check(self.global_activation_check)

    async def global_activation_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            await ctx.send(embed=error_embed("Commands only work in a server."))
            return False
        if not await self.repo.is_activated(str(ctx.guild.id)):
            url = self.settings.web_app_url
            await ctx.send(embed=error_embed(
                f"This server has not been activated.\n"
                f"The server owner must visit [{url}]({url}) "
                f"and sign in with Google to activate Jacky Music."
            ))
            return False
        return True

    def cog_unload(self) -> None:
        self.bot.remove_check(self.global_activation_check)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ActivationCheck(bot))
