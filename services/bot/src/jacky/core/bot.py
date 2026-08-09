"""JackyBot: dependency wiring and lifecycle. Everything is constructed here
and handed to the cogs/service as attributes — no globals, no import-time
side effects, so tests can build the graph with fakes."""

import logging

import aiohttp
import discord
import firebase_admin
from discord.ext import commands
from firebase_admin import credentials, firestore

from jacky.audio.node import LavalinkNode
from jacky.audio.player import PlayerService
from jacky.audio.provider import SingleNodeProvider
from jacky.commands.embeds import error_embed, now_playing_embed, success_embed
from jacky.config import Settings
from jacky.core.health import build_app, start_health_server
from jacky.state.listener import ServerDocListener
from jacky.state.repository import ServerRepository
from jacky.state.summon import SummonWatcher

log = logging.getLogger("jacky.bot")

BOT_PREFIX = "j!"

EXTENSIONS = [
    "jacky.commands.activation",
    "jacky.commands.playback",
    "jacky.commands.library",
    "jacky.commands.status",
]


class ChannelNotifier:
    """Routes PlayerService notifications to the guild's session text channel."""

    def __init__(self, bot: "JackyBot") -> None:
        self.bot = bot

    async def send(
        self,
        guild_id: int,
        *,
        text: str | None = None,
        track: dict | None = None,
        error: bool = False,
        text_channel_id: str | None = None,
    ) -> None:
        try:
            if text_channel_id is None:
                state = await self.bot.repo.get_state(str(guild_id)) or {}
                text_channel_id = state.get("textChannelId")
            if not text_channel_id:
                return
            channel = self.bot.get_channel(int(text_channel_id))
            if not channel:
                return
            if track is not None:
                embed = now_playing_embed(track)
            elif error:
                embed = error_embed(text or "")
            else:
                embed = success_embed(text or "")
            await channel.send(embed=embed)
        except Exception as exc:  # noqa: BLE001 — notifications are best-effort
            log.debug("notify failed for guild %s: %s", guild_id, exc)


class JackyBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix=BOT_PREFIX, intents=intents)
        self.settings = settings

        cred = credentials.Certificate(settings.firebase_credentials)
        firebase_admin.initialize_app(cred)
        db = firestore.client(database_id=settings.firestore_database)
        self.repo = ServerRepository(db)

        self.http_session: aiohttp.ClientSession | None = None
        self.node: LavalinkNode | None = None
        self.node_provider: SingleNodeProvider | None = None
        self.service: PlayerService | None = None
        self.token_store = None  # set in setup_hook when OAuth is configured
        self._health_runner = None

    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
        self.node = LavalinkNode(
            self.http_session,
            self.settings.lavalink_host,
            self.settings.lavalink_port,
            self.settings.lavalink_password,
            user_id=self.user.id,
        )
        self.node_provider = SingleNodeProvider(self.node)
        self.service = PlayerService(
            self,
            self.node_provider,
            self.repo,
            self.settings,
            ChannelNotifier(self),
            listener_factory=lambda gid: ServerDocListener(
                self, self.repo, self.service, str(gid)
            ),
        )
        self.node.on_ready = self.service.on_node_ready
        self.node.on_player_update = self.service.on_player_update
        self.node.on_track_start = self.service.on_track_start
        self.node.on_track_end = self.service.on_track_end
        self.node.on_track_exception = self.service.on_track_exception
        self.node.on_voice_ws_closed = self.service.on_voice_ws_closed
        self.node.start()

        for ext in EXTENSIONS:
            await self.load_extension(ext)
        self.summon_watcher = SummonWatcher(self, self.repo, self.service)
        self.summon_watcher.start()
        health_app = build_app(self, self.service)
        self.token_store = None
        if self.settings.discord_client_id and self.settings.discord_client_secret:
            from jacky.api.auth_routes import register_auth_routes
            from jacky.api.control import (
                _MEMBER_LOOKUP_ERRORS,
                register_control_routes,
            )
            from jacky.api.oauth import DiscordOAuth
            from jacky.api.ratelimit import SlidingWindow
            from jacky.api.tokens import TokenStore

            oauth = DiscordOAuth(
                self.http_session,
                self.settings.discord_client_id,
                self.settings.discord_client_secret,
                f"{self.settings.public_control_url}/control/auth/callback",
            )
            self.token_store = TokenStore(self.repo)

            async def member_gate(user_id: str) -> bool:
                # Activated-guild membership: cache first, REST fallback.
                # Runs once per sign-in, so per-guild REST calls are fine.
                for guild in self.guilds:
                    if not await self.repo.is_activated(str(guild.id)):
                        continue
                    if guild.get_member(int(user_id)):
                        return True
                    try:
                        await guild.fetch_member(int(user_id))
                        return True
                    except _MEMBER_LOOKUP_ERRORS:
                        continue
                return False

            register_auth_routes(
                health_app, oauth=oauth, token_store=self.token_store,
                member_gate=member_gate,
            )
            transcriber = None
            interpreter = None
            voice_dispatcher = None
            if self.settings.openai_api_key:
                from jacky.api.transcribe import OpenAITranscriber
                from jacky.api.voice_llm import LlmIntentInterpreter
                from jacky.voice_control import VoiceIntentDispatcher

                transcriber = OpenAITranscriber(
                    self.http_session,
                    self.settings.openai_api_key,
                    self.settings.openai_stt_model,
                )
                # Reuses OPENAI_API_KEY — no new credential. Without this the
                # route silently degrades to the fallback grammar parser.
                interpreter = LlmIntentInterpreter(
                    self.http_session,
                    self.settings.openai_api_key,
                    self.settings.openai_intent_model,
                )
                voice_dispatcher = VoiceIntentDispatcher(self.service, self.repo)
            register_control_routes(
                health_app, bot=self, service=self.service,
                token_store=self.token_store, limiter=SlidingWindow(),
                transcriber=transcriber, interpreter=interpreter,
                voice_dispatcher=voice_dispatcher,
            )
        self._health_runner = await start_health_server(
            self, self.service, self.settings.health_port, app=health_app
        )

    async def on_ready(self) -> None:
        log.info("Jacky Music online as %s (%d guilds)", self.user, len(self.guilds))
        try:
            await self.node.wait_ready(timeout=60)
        except TimeoutError:
            log.error("Lavalink not ready within 60s — convergence deferred to node ready")
        await self.service.converge_on_startup()

    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, (commands.CommandNotFound, commands.CheckFailure)):
            return
        if isinstance(error, commands.CommandInvokeError):
            log.error("command error in %s: %s", ctx.command, error.original,
                      exc_info=error.original)
            try:
                await ctx.send(embed=error_embed(f"Error: `{error.original}`"))
            except Exception:  # noqa: BLE001
                pass
            return
        log.error("command error in %s: %s", ctx.command, error)

    async def close(self) -> None:
        if getattr(self, "summon_watcher", None):
            self.summon_watcher.stop()
        if self._health_runner:
            await self._health_runner.cleanup()
        if self.node:
            await self.node.close()
        await super().close()
        if self.http_session:
            await self.http_session.close()
