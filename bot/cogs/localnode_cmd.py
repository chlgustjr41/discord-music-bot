import asyncio
import logging
import datetime
from datetime import timezone

import aiohttp
import discord
import wavelink
from discord.ext import commands, tasks

from utils.embeds import error_embed, success_embed

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10.0        # seconds to wait for initial connection
PROBE_TIMEOUT = 6.0           # HTTP health-check before connecting
MAX_RECONNECT_FAILURES = 3    # give up after N consecutive failures
HEALTH_CHECK_INTERVAL = 15    # seconds between health checks
GCP_NODE_ID = "gcp-primary"


class LocalNode(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs
        # guild_id -> local node identifier
        self.guild_node_map: dict[int, str] = {}
        # guild_id -> consecutive reconnect failure count
        self._reconnect_failures: dict[int, int] = {}
        self._check_local_nodes.start()

    def cog_unload(self):
        self._check_local_nodes.cancel()

    def _node_id(self, guild_id: int) -> str:
        return f"local-{guild_id}"

    # ------------------------------------------------------------------
    #  Node probe — fast HTTP check before committing to a connection
    # ------------------------------------------------------------------

    async def _probe_node(self, url: str, password: str) -> str | None:
        """HTTP health-check the Lavalink node. Returns None on success, error str on failure."""
        probe_url = url.rstrip("/") + "/version"
        try:
            timeout = aiohttp.ClientTimeout(total=PROBE_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(probe_url, headers={"Authorization": password}) as resp:
                    if resp.status == 200:
                        return None
                    return f"Node returned HTTP {resp.status} — is the password correct?"
        except aiohttp.ClientConnectorError:
            return f"Cannot reach {url} — is the tunnel running?"
        except asyncio.TimeoutError:
            return f"Probe timed out ({PROBE_TIMEOUT:.0f}s) — tunnel may not be ready yet."
        except Exception as e:
            return str(e)

    # ------------------------------------------------------------------
    #  Reusable connect / disconnect
    # ------------------------------------------------------------------

    async def connect_node(self, guild_id: int, url: str, password: str) -> str | None:
        """Connect a local Lavalink node for a guild.

        Returns None on success, or an error message string on failure.
        Never hangs: the HTTP probe guards against unreachable URLs and
        Pool.connect is wrapped in a timeout.
        """
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"

        node_id = self._node_id(guild_id)

        # Immediately reflect "connecting" state in Firestore so the UI doesn't
        # show a stale "Local Node" badge while we attempt a new connection.
        try:
            self.fs.set_local_node_status(str(guild_id), False)
        except Exception:
            pass  # Doc may not exist yet on first connect — that's fine

        # Fast probe to fail early without blocking the event loop
        probe_err = await self._probe_node(url, password)
        if probe_err:
            return probe_err

        # Eject any zombie node with the same ID from a previous session
        old_node = self._find_node(node_id)
        if old_node:
            try:
                await old_node.close(eject=True)
            except Exception:
                pass

        try:
            node = wavelink.Node(
                uri=url,
                password=password,
                identifier=node_id,
                inactive_player_timeout=None,
                inactive_channel_tokens=None,
            )
            await asyncio.wait_for(
                wavelink.Pool.connect(client=self.bot, nodes=[node]),
                timeout=CONNECT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return f"Connection timed out ({CONNECT_TIMEOUT:.0f}s). Tunnel may be slow to start."
        except Exception as e:
            log.error(f"Pool.connect failed for guild {guild_id}: {e}")
            return str(e)

        # Give wavelink a moment to complete the WebSocket handshake
        await asyncio.sleep(0.8)
        actual = self._find_node(node_id)
        if not actual or actual.status != wavelink.NodeStatus.CONNECTED:
            return "Node reached pool but did not enter CONNECTED status — check tunnel/password."

        self.guild_node_map[guild_id] = node_id
        self._reconnect_failures.pop(guild_id, None)
        self.fs.set_local_node(str(guild_id), url, password)
        log.info(f"Local node connected for guild {guild_id}: {url}")

        # Migrate any active player so audio actually routes through local Lavalink
        playback_cog = self.bot.cogs.get("Playback")
        if playback_cog:
            guild = self.bot.get_guild(guild_id)
            if guild and guild.voice_client:
                try:
                    await playback_cog.migrate_player_to_node(guild_id, actual)
                except Exception as e:
                    log.error(f"Player migration to local node failed for guild {guild_id}: {e}")

        return None

    async def disconnect_node(self, guild_id: int):
        """Disconnect the local node for a guild and migrate to GCP."""
        node_id = self._node_id(guild_id)
        local_node = self._find_node(node_id)

        # Migrate active playback to GCP before dropping the node
        playback_cog = self.bot.cogs.get("Playback")
        if playback_cog:
            guild = self.bot.get_guild(guild_id)
            if guild and guild.voice_client:
                await playback_cog.handle_node_failover(guild_id, notify=False)

        if local_node:
            try:
                await local_node.close(eject=True)
            except Exception:
                pass

        self.guild_node_map.pop(guild_id, None)
        self._reconnect_failures.pop(guild_id, None)
        self.fs.clear_local_node(str(guild_id))
        log.info(f"Local node disconnected for guild {guild_id}")

    # ------------------------------------------------------------------
    #  Discord commands
    # ------------------------------------------------------------------

    @commands.group(name="localnode", invoke_without_command=True, brief="Manage local audio node")
    async def localnode(self, ctx: commands.Context):
        """Connect a local Lavalink node for lower-latency audio.

        Run the setup from https://github.com/chlgustjr41/jacky-music-local
        then paste the j!localnode connect command it prints.

        Subcommands: connect, reconnect, disconnect, status"""
        await ctx.send(embed=error_embed(
            "Usage:\n"
            "`j!localnode connect <url> <password>` — connect to local node\n"
            "`j!localnode reconnect` — retry with saved credentials\n"
            "`j!localnode disconnect` — switch back to cloud\n"
            "`j!localnode status` — show current audio backend"
        ))

    @localnode.command(name="connect", brief="Connect to a local Lavalink node")
    async def connect(self, ctx: commands.Context, url: str, password: str):
        """Connect to a local Lavalink node via Cloudflare tunnel URL.

        The URL and password are printed by the setup script.

        Example: j!localnode connect https://abc123.trycloudflare.com mypassword"""
        guild_id = ctx.guild.id
        guild = self.bot.get_guild(guild_id)
        is_playing = bool(guild and guild.voice_client and guild.voice_client.playing)

        status_msg = await ctx.send(embed=discord.Embed(
            description=(
                f"🔌 Connecting to local node at `{url}`…\n"
                "This may take up to 10 seconds — please wait."
            ),
            color=0xFFA500,
        ))

        # Warn after 5s if the probe/connect is taking longer than expected
        async def _slow_warning():
            await asyncio.sleep(5)
            try:
                await status_msg.edit(embed=discord.Embed(
                    description=(
                        f"⏳ Still connecting to `{url}`…\n"
                        "Taking longer than usual — this can take up to **30 seconds**.\n"
                        "If this keeps happening, run `j!localnode reconnect` after the tunnel is ready, "
                        "or `j!localnode disconnect` to use cloud audio."
                    ),
                    color=0xFFA500,
                ))
            except Exception:
                pass

        warning_task = asyncio.create_task(_slow_warning())
        try:
            err = await self.connect_node(guild_id, url, password)
        finally:
            warning_task.cancel()

        if err:
            if "Cannot reach" in err or "Probe timed out" in err:
                hint = (
                    "**Cannot reach the tunnel.** Make sure your local node is running "
                    "and the Cloudflare tunnel is active, then try again."
                )
            elif "timed out" in err.lower():
                hint = (
                    "**Connection timed out.** The tunnel may still be starting — "
                    "wait 15–30 seconds and run `j!localnode reconnect`."
                )
            elif "HTTP" in err:
                hint = (
                    "**Wrong password or URL.** Double-check the tunnel URL and "
                    "password printed by the setup script."
                )
            else:
                hint = "Check your tunnel URL and password, then retry."

            cloud_note = (
                "\n\n✅ **Audio continues on cloud** — no interruption to playback.\n"
                "Run `j!localnode reconnect` to retry with the same credentials."
                if is_playing else
                "\nRun `j!localnode reconnect` to retry with the same credentials."
            )
            await status_msg.edit(embed=error_embed(
                f"Failed to connect to local node.\n\n{hint}{cloud_note}"
            ))
        else:
            if is_playing:
                await status_msg.edit(embed=discord.Embed(
                    description=(
                        f"✅ Connected to local node at `{url}`.\n"
                        "🔄 Migrating your session to the local node — "
                        "expect a brief pause while the track restarts."
                    ),
                    color=0x1DB954,
                ))
            else:
                await status_msg.edit(embed=success_embed(
                    f"Connected to local Lavalink node at `{url}`.\n"
                    "Future tracks will stream via this node for lower latency."
                ))

    @localnode.command(name="reconnect", brief="Retry with saved local node credentials")
    async def reconnect(self, ctx: commands.Context):
        """Reconnect to the local node using the last saved URL and password.

        Useful after the tunnel restarts without changing credentials.
        Example: j!localnode reconnect"""
        config = self.fs.get_local_node(str(ctx.guild.id))
        if not config:
            await ctx.send(embed=error_embed(
                "No saved credentials. Use `j!localnode connect <url> <password>` first."
            ))
            return
        url = config.get("url", "")
        password = config.get("password", "")
        if not url or not password:
            await ctx.send(embed=error_embed(
                "Saved credentials are incomplete. Use `j!localnode connect <url> <password>`."
            ))
            return
        await ctx.invoke(self.connect, url=url, password=password)

    @localnode.command(name="disconnect", brief="Switch back to cloud audio")
    async def disconnect(self, ctx: commands.Context):
        """Disconnect the local node and switch back to cloud audio.

        If music is playing, it migrates to the cloud node automatically."""
        guild_id = ctx.guild.id
        node_id = self._node_id(guild_id)

        if not self._find_node(node_id) and guild_id not in self.guild_node_map:
            local_config = self.fs.get_local_node(str(guild_id))
            if not local_config:
                await ctx.send(embed=error_embed("No local node is configured for this server."))
                return

        await self.disconnect_node(guild_id)
        await ctx.send(embed=success_embed("Local node disconnected. Using cloud audio."))

    @localnode.command(name="status", brief="Show which audio backend is active")
    async def status(self, ctx: commands.Context):
        """Show whether you're using the local or cloud Lavalink node."""
        guild_id = ctx.guild.id
        node_id = self._node_id(guild_id)
        local_config = self.fs.get_local_node(str(guild_id))
        failures = self._reconnect_failures.get(guild_id, 0)

        if not local_config:
            await ctx.send(embed=success_embed("Using **Cloud** Lavalink node (default)."))
            return

        local_node = self._find_node(node_id)
        node_actually_connected = bool(local_node and local_node.status == wavelink.NodeStatus.CONNECTED)
        firestore_says_connected = local_config.get("connected", False)

        # Sync Firestore immediately if it's out of date (e.g. cleanup event missed)
        if firestore_says_connected and not node_actually_connected:
            try:
                self.fs.set_local_node_status(str(guild_id), False)
                log.info(f"status command corrected stale Firestore for guild {guild_id}")
            except Exception as e:
                log.error(f"status command failed to sync Firestore for guild {guild_id}: {e}")

        embed = discord.Embed(color=0x1DB954)

        if node_actually_connected:
            connected_at = local_config.get("connectedAt")
            duration_str = ""
            if connected_at:
                try:
                    if hasattr(connected_at, "timestamp"):
                        dt = datetime.datetime.fromtimestamp(connected_at.timestamp(), tz=timezone.utc)
                    else:
                        dt = datetime.datetime.fromisoformat(str(connected_at))
                    delta = datetime.datetime.now(timezone.utc) - dt
                    hours, rem = divmod(int(delta.total_seconds()), 3600)
                    mins, _ = divmod(rem, 60)
                    duration_str = f" ({hours}h {mins}m)"
                except Exception:
                    pass

            embed.title = "🟢 Local Node Active"
            embed.description = (
                f"**URL:** `{local_config['url']}`\n"
                f"**Status:** Connected{duration_str}"
            )
        else:
            remaining = max(0, MAX_RECONNECT_FAILURES - failures)
            stale_note = " *(web UI synced)*" if firestore_says_connected else ""
            embed.title = "🔴 Local Node Unreachable"
            embed.description = (
                f"**URL:** `{local_config['url']}`\n"
                f"**Status:** Disconnected — using cloud{stale_note}\n"
                f"**Reconnect attempts left:** {remaining}/{MAX_RECONNECT_FAILURES}\n"
                "Run `j!localnode reconnect` to retry immediately."
            )
            embed.color = 0xFF9900

        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    #  Immediate UI sync — wavelink fires TrackEnd(reason=cleanup) when
    #  the node drops mid-track, which is faster than the 15s health check
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        # payload.reason is a TrackEndReason enum — use str() to compare safely
        if "cleanup" not in str(payload.reason).lower():
            return
        node = getattr(payload.player, "node", None)
        if not node:
            return
        for guild_id, node_id in list(self.guild_node_map.items()):
            if node_id == node.identifier:
                log.warning(
                    f"Local node {node_id} dropped mid-track for guild {guild_id} "
                    "— updating Firestore immediately"
                )
                try:
                    self.fs.set_local_node_status(str(guild_id), False)
                except Exception as e:
                    log.error(f"Failed to update local node status for guild {guild_id}: {e}")
                break

    # ------------------------------------------------------------------
    #  Health check loop — detect dead nodes, failover, auto-reconnect
    # ------------------------------------------------------------------

    @tasks.loop(seconds=HEALTH_CHECK_INTERVAL)
    async def _check_local_nodes(self):
        # --- Phase 1: detect and handle dead active nodes ---
        for guild_id, node_id in list(self.guild_node_map.items()):
            node = self._find_node(node_id)
            if node and node.status == wavelink.NodeStatus.CONNECTED:
                self._reconnect_failures.pop(guild_id, None)
                continue

            log.warning(f"Local node {node_id} is down for guild {guild_id} — failing over to GCP")

            # Eject the dead node from the pool BEFORE triggering failover so
            # wavelink.Pool.get_best_node() returns only the GCP node.
            if node:
                try:
                    await node.close(eject=True)
                except Exception:
                    pass

            self.guild_node_map.pop(guild_id, None)

            # Mark as disconnected (keep credentials for possible manual reconnect)
            try:
                self.fs.set_local_node_status(str(guild_id), False)
            except Exception as e:
                log.error(f"Failed to update local node status in Firestore for guild {guild_id}: {e}")

            # Trigger playback failover
            playback_cog = self.bot.cogs.get("Playback")
            if playback_cog:
                try:
                    await playback_cog.handle_node_failover(guild_id, notify=True)
                except Exception as e:
                    log.error(f"handle_node_failover failed for guild {guild_id}: {e}")

        # --- Phase 2: attempt to reconnect guilds with saved-but-disconnected nodes ---
        for guild in self.bot.guilds:
            guild_id = guild.id
            if guild_id in self.guild_node_map:
                continue  # Already active

            failures = self._reconnect_failures.get(guild_id, 0)
            if failures >= MAX_RECONNECT_FAILURES:
                continue  # Gave up; user must reconnect manually

            config = self.fs.get_local_node(str(guild_id))
            if not config or config.get("connected", True):
                continue  # No saved config or it's already marked connected

            url = config.get("url", "")
            password = config.get("password", "")
            if not url or not password:
                continue

            log.info(
                f"Reconnect attempt {failures + 1}/{MAX_RECONNECT_FAILURES} "
                f"for guild {guild_id}: {url}"
            )
            err = await self.connect_node(guild_id, url, password)

            if err:
                new_count = failures + 1
                self._reconnect_failures[guild_id] = new_count
                log.warning(
                    f"Reconnect failed for guild {guild_id} "
                    f"({new_count}/{MAX_RECONNECT_FAILURES}): {err}"
                )
                if new_count == 1:
                    # First failure — warn the user and explain the auto-retry window
                    remaining_secs = (MAX_RECONNECT_FAILURES - 1) * HEALTH_CHECK_INTERVAL
                    await self._notify_channel(
                        guild_id,
                        f"⚠️ Local node is unreachable — auto-retry in progress.\n"
                        f"Will retry up to {MAX_RECONNECT_FAILURES - 1} more times "
                        f"(~{remaining_secs}s). Audio is running on cloud.\n"
                        "Run `j!localnode reconnect` at any time to retry immediately.",
                        error=True,
                    )
                elif new_count >= MAX_RECONNECT_FAILURES:
                    log.warning(
                        f"Max reconnect attempts reached for guild {guild_id} — "
                        "clearing local node config."
                    )
                    self.fs.clear_local_node(str(guild_id))
                    self._reconnect_failures.pop(guild_id, None)
                    await self._notify_channel(
                        guild_id,
                        "❌ Local node unreachable after all retry attempts — switched to cloud.\n"
                        "Start your local node and run `j!localnode connect <url> <password>` "
                        "to reconnect.",
                        error=True,
                    )
            else:
                log.info(f"Auto-reconnected local node for guild {guild_id}")
                await self._notify_channel(guild_id, "✅ Local node reconnected — audio routed via local node.")

        # --- Phase 3: Firestore reconciliation ---
        # Catch any guild where Firestore still shows connected=true but our active
        # map no longer tracks it (e.g. cleanup event fired, on_wavelink_track_end
        # cleared the map entry, but Firestore was not updated due to a transient error).
        for guild in self.bot.guilds:
            guild_id = guild.id
            if guild_id in self.guild_node_map:
                continue  # Actively tracked — Phase 1 handles it
            try:
                config = self.fs.get_local_node(str(guild_id))
                if not config or not config.get("connected", False):
                    continue
                # Firestore says connected but we have no active node in memory
                node_id = self._node_id(guild_id)
                node = self._find_node(node_id)
                if node and node.status == wavelink.NodeStatus.CONNECTED:
                    # Node is actually alive — re-add to map so Phase 1 tracks it
                    log.info(f"Phase 3: re-tracking live local node {node_id} for guild {guild_id}")
                    self.guild_node_map[guild_id] = node_id
                else:
                    # Firestore says connected but node is gone — fix it
                    log.warning(
                        f"Phase 3: Firestore stale for guild {guild_id} "
                        "(connected=true but no active node) — correcting"
                    )
                    self.fs.set_local_node_status(str(guild_id), False)
            except Exception as e:
                log.error(f"Phase 3 reconciliation failed for guild {guild_id}: {e}")

    @_check_local_nodes.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()
        await self._auto_reconnect_on_startup()

    async def _auto_reconnect_on_startup(self):
        """On startup, try to reconnect any persisted local nodes (with timeout)."""
        for guild in self.bot.guilds:
            try:
                config = self.fs.get_local_node(str(guild.id))
                if not config:
                    continue
                url = config.get("url", "")
                password = config.get("password", "")
                if not url or not password:
                    continue
                log.info(f"Startup auto-reconnect for guild {guild.id}: {url}")
                err = await self.connect_node(guild.id, url, password)
                if err:
                    log.warning(f"Startup reconnect failed for guild {guild.id}: {err}")
                    # Mark disconnected so the health check loop can retry
                    self.fs.set_local_node_status(str(guild.id), False)
                else:
                    log.info(f"Startup reconnect success for guild {guild.id}")
            except Exception as e:
                log.error(f"Startup auto-reconnect error for guild {guild.id}: {e}")

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    async def _notify_channel(self, guild_id: int, message: str, error: bool = False):
        """Post a notification to the server's last text channel."""
        try:
            state = self.fs.get_server_state(str(guild_id))
            ch_id = state.get("textChannelId") if state else None
            if not ch_id:
                return
            ch = self.bot.get_channel(int(ch_id))
            if ch:
                embed = error_embed(message) if error else success_embed(message)
                await ch.send(embed=embed)
        except Exception:
            pass

    def _find_node(self, identifier: str) -> wavelink.Node | None:
        for node in wavelink.Pool.nodes.values():
            if node.identifier == identifier:
                return node
        return None

    def get_node_for_guild(self, guild_id: int) -> wavelink.Node | None:
        """Return the active local node for a guild, or None to use GCP default."""
        node_id = self.guild_node_map.get(guild_id)
        if not node_id:
            return None
        node = self._find_node(node_id)
        if node and node.status == wavelink.NodeStatus.CONNECTED:
            return node
        return None


async def setup(bot: commands.Bot):
    await bot.add_cog(LocalNode(bot))
