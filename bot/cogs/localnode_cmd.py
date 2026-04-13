import logging
import datetime
from datetime import timezone

import discord
import wavelink
from discord.ext import commands, tasks

from utils.embeds import error_embed, success_embed

log = logging.getLogger(__name__)


class LocalNode(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs
        # guild_id -> node identifier
        self.guild_node_map: dict[int, str] = {}
        self._check_local_nodes.start()

    def cog_unload(self):
        self._check_local_nodes.cancel()

    def _node_id(self, guild_id: int) -> str:
        return f"local-{guild_id}"

    # ------------------------------------------------------------------
    #  Reusable connect / disconnect (used by both Discord cmds and web)
    # ------------------------------------------------------------------

    async def connect_node(self, guild_id: int, url: str, password: str) -> str | None:
        """Connect a local Lavalink node for a guild.

        Returns None on success, or an error message string on failure.
        """
        node_id = self._node_id(guild_id)

        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"

        try:
            node = wavelink.Node(
                uri=url,
                password=password,
                identifier=node_id,
                inactive_player_timeout=None,
                inactive_channel_tokens=None,
            )
            await wavelink.Pool.connect(client=self.bot, nodes=[node])
        except Exception as e:
            log.error(f"Failed to connect local node for guild {guild_id}: {e}")
            return str(e)

        self.guild_node_map[guild_id] = node_id
        self.fs.set_local_node(str(guild_id), url, password)
        log.info(f"Local node connected for guild {guild_id}: {url}")
        return None

    async def disconnect_node(self, guild_id: int):
        """Disconnect the local node for a guild and migrate to GCP."""
        node_id = self._node_id(guild_id)
        local_node = self._find_node(node_id)

        # If a player is active on the local node, migrate to GCP
        guild = self.bot.get_guild(guild_id)
        if guild:
            player = guild.voice_client
            if player and local_node:
                player_node_id = getattr(player.node, "identifier", None)
                if player_node_id == node_id:
                    await self._migrate_to_gcp(guild_id)

        self.guild_node_map.pop(guild_id, None)
        self.fs.clear_local_node(str(guild_id))
        log.info(f"Local node disconnected for guild {guild_id}")

    # ------------------------------------------------------------------
    #  Discord commands
    # ------------------------------------------------------------------

    @commands.group(name="localnode", invoke_without_command=True, brief="Manage local audio node for lower latency")
    async def localnode(self, ctx: commands.Context):
        """Connect a local Lavalink audio node for lower latency streaming.

        Run the local node setup from https://github.com/chlgustjr41/jacky-music-local
        then use the tunnel URL and password printed by the setup script.

        Subcommands: connect, disconnect, status"""
        await ctx.send(embed=error_embed(
            "Usage: `j!localnode connect <url> <password>` | `j!localnode disconnect` | `j!localnode status`"
        ))

    @localnode.command(name="connect", brief="Connect to a local Lavalink node")
    async def connect(self, ctx: commands.Context, url: str, password: str):
        """Connect to a local Lavalink node via Cloudflare tunnel URL.

        The URL and password are printed by the setup script.

        Example: j!localnode connect https://abc123.trycloudflare.com mypassword"""
        err = await self.connect_node(ctx.guild.id, url, password)
        if err:
            await ctx.send(embed=error_embed(f"Failed to connect to local node: {err}"))
        else:
            await ctx.send(embed=success_embed(
                f"Connected to local Lavalink node at `{url}`.\n"
                "New tracks will use this node for lower latency."
            ))

    @localnode.command(name="disconnect", brief="Switch back to cloud audio")
    async def disconnect(self, ctx: commands.Context):
        """Disconnect the local node and switch back to the default cloud audio backend.

        If music is playing, it will be migrated to the cloud node automatically."""
        guild_id = ctx.guild.id
        node_id = self._node_id(guild_id)

        local_node = self._find_node(node_id)
        if not local_node and guild_id not in self.guild_node_map:
            await ctx.send(embed=error_embed("No local node is configured for this server."))
            return

        await self.disconnect_node(guild_id)
        await ctx.send(embed=success_embed("Local node disconnected. Using cloud node."))

    @localnode.command(name="status", brief="Show which audio backend is active")
    async def status(self, ctx: commands.Context):
        """Show whether you're using a local node or the cloud backend, and connection details."""
        guild_id = ctx.guild.id
        node_id = self._node_id(guild_id)
        local_config = self.fs.get_local_node(str(guild_id))

        if not local_config:
            await ctx.send(embed=success_embed("Using **Cloud** Lavalink node (default)."))
            return

        local_node = self._find_node(node_id)
        embed = discord.Embed(color=0x1DB954)

        if local_node and local_node.status == wavelink.NodeStatus.CONNECTED:
            connected_at = local_config.get("connectedAt")
            duration_str = ""
            if connected_at:
                try:
                    if hasattr(connected_at, "timestamp"):
                        dt = datetime.datetime.fromtimestamp(connected_at.timestamp(), tz=timezone.utc)
                    else:
                        dt = datetime.datetime.fromisoformat(str(connected_at))
                    delta = datetime.datetime.now(timezone.utc) - dt
                    hours, remainder = divmod(int(delta.total_seconds()), 3600)
                    minutes, _ = divmod(remainder, 60)
                    duration_str = f" ({hours}h {minutes}m)"
                except Exception:
                    duration_str = ""

            embed.title = "Local Node Active"
            embed.description = (
                f"**URL:** `{local_config['url']}`\n"
                f"**Status:** Connected{duration_str}"
            )
        else:
            embed.title = "Local Node Down"
            embed.description = (
                f"**URL:** `{local_config['url']}`\n"
                f"**Status:** Disconnected — falling back to cloud node"
            )
            embed.color = 0xFF9900

        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    #  Health check loop: detect dead local nodes and failover
    # ------------------------------------------------------------------

    @tasks.loop(seconds=15)
    async def _check_local_nodes(self):
        # Check active nodes for failure
        for guild_id, node_id in list(self.guild_node_map.items()):
            node = self._find_node(node_id)
            if node and node.status == wavelink.NodeStatus.CONNECTED:
                continue

            log.warning(f"Local node {node_id} is down for guild {guild_id} — failing over to GCP")
            await self._migrate_to_gcp(guild_id)

            self.guild_node_map.pop(guild_id, None)
            # Keep credentials in Firestore for auto-reconnect; just mark as disconnected
            try:
                self.fs.set_local_node_status(str(guild_id), False)
            except Exception:
                pass

            # Notify in text channel
            state = self.fs.get_server_state(str(guild_id))
            text_channel_id = state.get("textChannelId") if state else None
            if text_channel_id:
                ch = self.bot.get_channel(int(text_channel_id))
                if ch:
                    try:
                        await ch.send(embed=success_embed(
                            "Local node disconnected — switched back to cloud."
                        ))
                    except Exception:
                        pass

        # Try to reconnect saved nodes that aren't currently active
        for guild in self.bot.guilds:
            if guild.id in self.guild_node_map:
                continue
            try:
                config = self.fs.get_local_node(str(guild.id))
                if not config or config.get("connected", True):
                    # No config, or already marked connected (shouldn't happen)
                    continue
                url = config.get("url", "")
                password = config.get("password", "")
                if not url or not password:
                    continue
                log.info(f"Attempting to reconnect local node for guild {guild.id}")
                err = await self.connect_node(guild.id, url, password)
                if err:
                    log.debug(f"Reconnect attempt failed for guild {guild.id}: {err}")
                else:
                    log.info(f"Successfully reconnected local node for guild {guild.id}")
            except Exception:
                pass

    @_check_local_nodes.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()
        # Auto-reconnect persisted local nodes from Firestore
        await self._auto_reconnect()

    async def _auto_reconnect(self):
        """On startup, reconnect any persisted local nodes."""
        for guild in self.bot.guilds:
            try:
                config = self.fs.get_local_node(str(guild.id))
                if not config:
                    continue
                url = config.get("url", "")
                password = config.get("password", "")
                if not url or not password:
                    continue

                log.info(f"Auto-reconnecting local node for guild {guild.id}: {url}")
                err = await self.connect_node(guild.id, url, password)
                if err:
                    log.warning(f"Auto-reconnect failed for guild {guild.id}: {err}")
                    # Don't clear the config — user may restart the local node later
                else:
                    log.info(f"Auto-reconnected local node for guild {guild.id}")
            except Exception as e:
                log.error(f"Error during auto-reconnect for guild {guild.id}: {e}")

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    async def _migrate_to_gcp(self, guild_id: int):
        """Migrate a player from a local node to the GCP node."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        state = self.fs.get_server_state(str(guild_id))
        if not state:
            return

        # Disconnect current player if connected
        player = guild.voice_client
        if player:
            try:
                await player.disconnect()
            except Exception:
                pass

        current_track = state.get("currentTrack")
        voice_channel_id = state.get("voiceChannelId")
        if not voice_channel_id or not current_track:
            return

        channel = self.bot.get_channel(int(voice_channel_id))
        if not channel:
            return

        try:
            from player import JackyPlayer
            new_player = await channel.connect(cls=JackyPlayer)
            new_player.autoplay = wavelink.AutoPlayMode.disabled

            results = await wavelink.Playable.search(current_track["url"])
            if not results:
                return

            from cogs.playback import _first_track
            playable = _first_track(results)
            if not playable:
                return

            # Calculate elapsed time to seek to approximate position
            start_ms = 0
            started_at = current_track.get("startedAt")
            if started_at:
                try:
                    dt = datetime.datetime.fromisoformat(started_at)
                    elapsed = (datetime.datetime.now(timezone.utc) - dt).total_seconds()
                    start_ms = int(max(0, elapsed) * 1000)
                except Exception:
                    pass

            await new_player.play(playable, start=start_ms)
        except Exception as e:
            log.error(f"Failed to migrate player to GCP for guild {guild_id}: {e}")

    def _find_node(self, identifier: str) -> wavelink.Node | None:
        for node in wavelink.Pool.nodes.values():
            if node.identifier == identifier:
                return node
        return None

    def get_node_for_guild(self, guild_id: int) -> wavelink.Node | None:
        """Return the preferred node for a guild, or None for default."""
        node_id = self.guild_node_map.get(guild_id)
        if not node_id:
            return None
        node = self._find_node(node_id)
        if node and node.status == wavelink.NodeStatus.CONNECTED:
            return node
        return None


async def setup(bot: commands.Bot):
    await bot.add_cog(LocalNode(bot))
