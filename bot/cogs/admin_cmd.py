"""Bot management commands — available to all users in any server.

Requires:
  - /var/run/docker.sock mounted into the bot container (docker-compose.yml)
  - `docker` Python package installed (requirements.txt)
"""
import asyncio
import logging
import datetime

import discord
from discord.ext import commands

from utils.embeds import error_embed, success_embed

log = logging.getLogger(__name__)

try:
    import docker as docker_sdk
    _DOCKER_OK = True
except ImportError:
    _DOCKER_OK = False
    log.warning("docker Python package not installed — j!bot commands will be unavailable")

# Map of friendly name -> container name on the host
CONTAINERS = {
    "bot": "jacky-bot",
    "lavalink": "jacky-lavalink",
}


class BotCmd(commands.Cog):
    """Bot management commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _client(self) -> "docker_sdk.DockerClient":
        if not _DOCKER_OK:
            raise RuntimeError(
                "`docker` Python package is not installed. "
                "Add it to requirements.txt and rebuild the bot container."
            )
        return docker_sdk.from_env(timeout=5)

    # ------------------------------------------------------------------
    #  Command group
    # ------------------------------------------------------------------

    @commands.group(name="bot", invoke_without_command=True, brief="Bot management commands")
    async def manage(self, ctx: commands.Context):
        """Check status, view logs, or restart the bot and Lavalink.

        Subcommands:
          j!bot restart [bot|lavalink|all]  — restart containers
          j!bot status                       — show container status
          j!bot logs [bot|lavalink] [lines]  — view recent logs"""
        embed = discord.Embed(
            title="Bot Commands",
            color=0x6c63ff,
            description=(
                "`j!bot restart [bot|lavalink|all]` — restart containers\n"
                "`j!bot status` — show container health\n"
                "`j!bot logs [bot|lavalink] [lines]` — tail container logs"
            ),
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    #  j!bot restart
    # ------------------------------------------------------------------

    @manage.command(name="restart", brief="Restart containers")
    async def restart(self, ctx: commands.Context, target: str = "bot"):
        """Restart one or both containers.

        Targets:
          bot      — restart only the Discord bot (default)
          lavalink — restart only Lavalink
          all      — restart Lavalink first, then the bot

        Examples:
          j!bot restart
          j!bot restart lavalink
          j!bot restart all"""
        target = target.lower()
        if target not in ("bot", "lavalink", "all"):
            await ctx.send(embed=error_embed(
                "Unknown target. Use `bot`, `lavalink`, or `all`."
            ))
            return

        if target == "all":
            names = [CONTAINERS["lavalink"], CONTAINERS["bot"]]
        else:
            names = [CONTAINERS[target]]

        restarting_self = CONTAINERS["bot"] in names

        if restarting_self:
            await ctx.send(embed=success_embed(
                f"Restarting `{'`, `'.join(names)}`...\n"
                "Bot will be offline for ~15 seconds."
            ))
        else:
            await ctx.send(embed=success_embed(
                f"Restarting `{'`, `'.join(names)}`..."
            ))

        async def _do():
            def _sync():
                client = self._client()
                for name in names:
                    try:
                        c = client.containers.get(name)
                        log.info(f"Restarting container: {name}")
                        c.restart(timeout=10)
                        log.info(f"Restart sent for: {name}")
                    except Exception as e:
                        raise RuntimeError(f"Failed to restart `{name}`: {e}")

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _sync)
            if not restarting_self:
                await ctx.send(embed=success_embed("Restart complete."))

        if restarting_self:
            async def _delayed():
                await asyncio.sleep(0.4)
                await _do()
            asyncio.create_task(_delayed())
        else:
            try:
                await _do()
            except Exception as e:
                await ctx.send(embed=error_embed(str(e)))

    # ------------------------------------------------------------------
    #  j!bot status
    # ------------------------------------------------------------------

    @manage.command(name="status", brief="Show container health")
    async def status(self, ctx: commands.Context):
        """Show the status and uptime of the bot and Lavalink containers."""
        def _sync():
            client = self._client()
            rows = []
            for label, name in CONTAINERS.items():
                try:
                    c = client.containers.get(name)
                    state = c.attrs.get("State", {})
                    status = state.get("Status", "unknown")
                    started_raw = state.get("StartedAt", "")
                    uptime = ""
                    if started_raw and status == "running":
                        try:
                            started = datetime.datetime.fromisoformat(
                                started_raw[:26].replace("Z", "+00:00")
                            )
                            delta = datetime.datetime.now(datetime.timezone.utc) - started
                            h, rem = divmod(int(delta.total_seconds()), 3600)
                            m, s = divmod(rem, 60)
                            uptime = f" (up {h}h {m}m {s}s)"
                        except Exception:
                            uptime = ""
                    icon = "🟢" if status == "running" else "🔴"
                    rows.append(f"{icon} **{name}** — {status}{uptime}")
                except Exception as e:
                    rows.append(f"🔴 **{name}** — error: {e}")
            return rows

        try:
            loop = asyncio.get_event_loop()
            rows = await loop.run_in_executor(None, _sync)
            embed = discord.Embed(
                title="Container Status",
                description="\n".join(rows),
                color=0x1DB954,
                timestamp=datetime.datetime.now(datetime.timezone.utc),
            )
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(embed=error_embed(f"Could not reach Docker: {e}"))

    # ------------------------------------------------------------------
    #  j!bot logs
    # ------------------------------------------------------------------

    @manage.command(name="logs", brief="Tail container logs")
    async def logs(self, ctx: commands.Context, service: str = "bot", lines: int = 20):
        """Show recent log output from a container.

        Services: bot, lavalink (default: bot)
        Lines: 1–50 (default: 20)

        Examples:
          j!bot logs
          j!bot logs lavalink 30"""
        name = CONTAINERS.get(service.lower())
        if not name:
            await ctx.send(embed=error_embed("Service must be `bot` or `lavalink`."))
            return

        lines = max(1, min(50, lines))

        def _sync():
            client = self._client()
            c = client.containers.get(name)
            raw = c.logs(tail=lines, timestamps=True)
            return raw.decode("utf-8", errors="replace").strip()

        try:
            loop = asyncio.get_event_loop()
            output = await loop.run_in_executor(None, _sync)
            if not output:
                output = "(no output)"
            if len(output) > 1850:
                output = "...\n" + output[-1850:]
            await ctx.send(
                f"**`{name}` — last {lines} lines:**\n```\n{output}\n```"
            )
        except Exception as e:
            await ctx.send(embed=error_embed(f"Failed to read logs: {e}"))


async def setup(bot: commands.Bot):
    await bot.add_cog(BotCmd(bot))
