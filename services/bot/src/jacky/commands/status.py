"""j!status — one-command health view of the whole stack, from inside Discord.

Combines the bot's own state (gateway, audio node, this guild's player)
with the guardian's probe snapshot fetched over the compose network. The
guardian being unreachable is itself a reported condition, not an error.
"""

import logging
import time

import discord
from discord.ext import commands

from jacky import __version__
from jacky.commands.embeds import EMBED_COLOR, error_embed, success_embed

log = logging.getLogger("jacky.status")

PLAYBOOK_HINTS = {
    "F1": "poToken rejected — auto-fixed by a fresh mint",
    "F2": "YouTube OAuth revoked — operator: `make reauth`",
    "F3": "youtube-source plugin broken by a YouTube change — wait for/apply plugin update",
    "F4": "Lavalink sick — auto-restarted",
    "F5": "bot hung — auto-restarted",
    "F6": "silent playback — bot auto-restarted",
}


def _fmt_uptime(seconds: float) -> str:
    minutes, _ = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _fmt_position(position_ms: int, duration_s: int) -> str:
    pos_m, pos_s = divmod(position_ms // 1000, 60)
    if duration_s:
        dur_m, dur_s = divmod(duration_s, 60)
        return f"{pos_m}:{pos_s:02d} / {dur_m}:{dur_s:02d}"
    return f"{pos_m}:{pos_s:02d}"


class Status(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.started_at = time.monotonic()

    async def _guardian_status(self) -> dict | None:
        url = self.bot.settings.guardian_status_url
        try:
            async with self.bot.http_session.get(url) as resp:
                if resp.status != 200:
                    return None
                return await resp.json(content_type=None)
        except Exception as exc:  # noqa: BLE001 — reported as "unreachable"
            log.debug("guardian status fetch failed: %s", exc)
            return None

    @commands.command(name="status", aliases=["health"], brief="Bot + guardian health check")
    async def status(self, ctx: commands.Context) -> None:
        """Show the health of the whole stack: bot, audio node, this server's
        player, and the guardian's latest probe verdict."""
        embed = discord.Embed(title="🩺 Jacky Music — System Status", color=EMBED_COLOR)

        # ── bot ──────────────────────────────────────────────────────────
        node = self.bot.node
        gateway_ms = int(self.bot.latency * 1000)
        embed.add_field(
            name="Bot",
            value=(
                f"v{__version__} · up {_fmt_uptime(time.monotonic() - self.started_at)}\n"
                f"Gateway ping **{gateway_ms}ms** · {len(self.bot.guilds)} guilds"
            ),
            inline=False,
        )

        # ── audio node ───────────────────────────────────────────────────
        node_line = "🟢 connected" if node and node.connected else "🔴 disconnected"
        if node and node.session_id:
            node_line += f" · session `{node.session_id}`"
        embed.add_field(name="Audio node (Lavalink)", value=node_line, inline=False)

        # ── this server's player ─────────────────────────────────────────
        state = await self.bot.repo.get_state(str(ctx.guild.id)) or {}
        position = self.bot.service.positions.get(ctx.guild.id) or {}
        current = state.get("currentTrack")
        if current:
            voice_ok = position.get("connected")
            player_line = (
                f"{'🟢' if voice_ok else '🔴'} voice "
                f"{'connected' if voice_ok else 'NOT connected'}\n"
                f"**{current.get('title', '?')}** — "
                f"{_fmt_position(position.get('position', 0), current.get('duration', 0))}"
                f"{' (paused)' if state.get('isPaused') else ''}\n"
                f"Queue: {len(state.get('queue', []))} track(s)"
            )
        elif state.get("voiceChannelId"):
            player_line = f"🟡 idle in **{state.get('voiceChannelName', 'voice')}** — nothing playing"
        else:
            player_line = "⚪ no active session"
        embed.add_field(name="This server", value=player_line, inline=False)

        # ── guardian ─────────────────────────────────────────────────────
        guardian = await self._guardian_status()
        if guardian is None:
            embed.add_field(
                name="Guardian",
                value="🔴 unreachable — the supervisor may be down (check `make logs s=guardian`)",
                inline=False,
            )
        else:
            canary = guardian.get("canary") or {}
            if canary.get("ok"):
                canary_line = "🟢 canary passing (YouTube reachable through Lavalink)"
            elif canary:
                playbook = canary.get("playbook", "F?")
                hint = PLAYBOOK_HINTS.get(playbook, "see runbook")
                error = (canary.get("error") or "")[:120]
                canary_line = f"🔴 **[{playbook}]** {hint}\n`{error}`"
            else:
                canary_line = "🟡 no probe completed yet"
            active = guardian.get("activeFailures") or []
            if active:
                canary_line += f"\nActive failures: {', '.join(active)}"
            canary_line += f"\nUp {_fmt_uptime(guardian.get('uptimeSeconds', 0))}"
            last_probe = guardian.get("lastProbeAt")
            if last_probe:
                canary_line += f" · last probe {last_probe} UTC"
            embed.add_field(name="Guardian", value=canary_line, inline=False)

            actions = guardian.get("actions") or []
            if actions:
                lines = [
                    f"`{a['at'][11:16]}` **[{a['playbook']}]** {a['action']}"
                    for a in actions[-5:]
                ]
                embed.add_field(
                    name="Recent automated actions", value="\n".join(lines), inline=False
                )

        embed.set_footer(text="Playbook details: docs/operations/RUNBOOK.md")
        await ctx.send(embed=embed)

    @commands.command(name="unlink", brief="Revoke your Stream Deck sign-ins")
    async def unlink(self, ctx: commands.Context) -> None:
        """Revoke every Stream Deck control token minted for your Discord
        account. Sign in again from the Stream Deck to re-link."""
        token_store = getattr(self.bot, "token_store", None)
        if token_store is None:
            await ctx.send(embed=error_embed("Stream Deck control is not enabled."))
            return
        count = await token_store.revoke_user(str(ctx.author.id))
        if count > 0:
            msg = f"Unlinked {count} Stream Deck sign-in(s)."
        else:
            msg = "No active sign-ins."
        await ctx.send(embed=success_embed(msg))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Status(bot))
