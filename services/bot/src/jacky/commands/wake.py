"""j!wake — show or set this server's voice wake phrase."""

import logging

from discord.ext import commands

from jacky.commands.embeds import error_embed, success_embed

log = logging.getLogger("jacky.commands.wake")


class Wake(commands.Cog):
    def __init__(self, bot, repo, notifier):
        self.bot, self.repo, self.notifier = bot, repo, notifier

    @commands.command(name="wake", brief="Show or set the voice wake phrase")
    @commands.has_guild_permissions(manage_guild=True)
    async def wake(self, ctx: commands.Context, *, phrase: str = "") -> None:
        sid = str(ctx.guild.id)
        if not phrase:
            state = await self.repo.get_state(sid) or {}
            current = state.get("wakePhrase") or "hey jacky"
            await ctx.send(embed=success_embed(f'Wake phrase: **"{current}"**'))
            return
        verdict = await self.notifier.validate_phrase(phrase)
        if verdict is None:
            await ctx.send(embed=error_embed(
                "Voice control is offline right now — try again later."
            ))
            return
        if not verdict["ok"]:
            await ctx.send(embed=error_embed(
                "Can't use that phrase: " + "; ".join(verdict["problems"])
            ))
            return
        await self.repo.update_state(sid, {"wakePhrase": phrase.lower().strip()})
        # Re-push to the listener if a session is live so it takes effect now.
        state = await self.repo.get_state(sid) or {}
        if state.get("voiceChannelId") and ctx.voice_client:
            await self.notifier.session_started(ctx.guild.id, state["voiceChannelId"])
        await ctx.send(embed=success_embed(f'Wake phrase set to **"{phrase}"**'))


async def setup(bot: commands.Bot) -> None:
    if getattr(bot, "voice_notifier", None):
        await bot.add_cog(Wake(bot, bot.repo, bot.voice_notifier))
