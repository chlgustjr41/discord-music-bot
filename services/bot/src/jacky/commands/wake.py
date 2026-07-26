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

    @commands.command(name="ears", brief="Check the voice-control (Hey Jacky) listener")
    async def ears(self, ctx: commands.Context) -> None:
        """Report whether Jacky Ears is listening here, and how to test it."""
        sid = str(ctx.guild.id)
        state = await self.repo.get_state(sid) or {}
        phrase = state.get("wakePhrase") or "hey jacky"
        status = await self.notifier.get_status()
        if status is None:
            await ctx.send(embed=error_embed(
                "🔇 Jacky Ears (voice control) is **offline**. It only runs when the "
                "stack is started with the `voice` profile."
            ))
            return
        here = (status.get("guilds") or {}).get(sid)
        if not here or not here.get("connected"):
            await ctx.send(embed=error_embed(
                "🎧 Jacky Ears is online but **not in a voice channel here**.\n"
                f"Run `j!start` to open a session (it joins automatically), then say "
                f'**"{phrase}"** and a command.'
            ))
            return
        await ctx.send(embed=success_embed(
            f"🎤 **Jacky Ears is listening** in **{here['channel']}**.\n"
            f'Wake phrase: **"{here["wake_phrase"]}"**\n\n'
            f'**To test:** say **"{here["wake_phrase"]}"**, wait for the ring tone, '
            f"then a command — *skip*, *pause*, *resume*, *volume up*, *volume down*, "
            f"*stop*, or *play <song>*."
        ))


async def setup(bot: commands.Bot) -> None:
    if getattr(bot, "voice_notifier", None):
        await bot.add_cog(Wake(bot, bot.repo, bot.voice_notifier))
