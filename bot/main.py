import logging

import discord
from discord.ext import commands
import firebase_admin
from firebase_admin import credentials, firestore
import wavelink

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)

from config import (
    DISCORD_TOKEN,
    FIREBASE_SERVICE_ACCOUNT_KEY,
    LAVALINK_HOST,
    LAVALINK_PORT,
    LAVALINK_PASSWORD,
    BOT_PREFIX,
)
from services.firestore_client import FirestoreClient

# Firebase init
cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_KEY)
firebase_admin.initialize_app(cred)
db = firestore.client(database_id="discord-music-bot")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents)
bot.fs = FirestoreClient(db)


@bot.event
async def on_ready():
    log.info(f"Jacky Music is online as {bot.user}")
    log.info(f"Guilds: {[g.name for g in bot.guilds]}")
    log.info(f"Loaded cogs: {list(bot.cogs.keys())}")
    log.info(f"Commands: {[c.name for c in bot.commands]}")
    await _cleanup_stale_sessions()


async def _cleanup_stale_sessions():
    """On startup, reset any servers that Firestore still shows as isPlaying=True.

    This prevents stale UI state after a bot crash or container restart.
    The current track is moved back to the front of the queue so users can
    resume where they left off.
    """
    try:
        docs = list(db.collection("servers").stream())
        cleaned = 0
        for doc in docs:
            state = doc.to_dict()
            if not state.get("isPlaying") and not state.get("isPaused"):
                continue
            updates: dict = {"isPlaying": False, "isPaused": False}
            current = state.get("currentTrack")
            if current:
                # Strip ephemeral fields then prepend back to queue
                queue_item = {k: v for k, v in current.items()
                              if k not in ("startedAt", "seekPosition")}
                queue = state.get("queue", [])
                updates["queue"] = [queue_item] + queue
                updates["currentTrack"] = None
            doc.reference.update(updates)
            cleaned += 1
        if cleaned:
            log.info(f"Cleaned up stale isPlaying state for {cleaned} server(s)")
    except Exception as e:
        log.error(f"Failed to clean up stale sessions: {e}")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return  # Ignore unknown commands silently
    if isinstance(error, commands.CheckFailure):
        return  # Activation/permission checks already send their own message
    if isinstance(error, commands.CommandInvokeError):
        log.error(f"Command error in {ctx.command}: {error.original}", exc_info=error.original)
        try:
            await ctx.send(f"❌ Error: `{error.original}`")
        except Exception:
            pass
        return
    log.error(f"Command error in {ctx.command}: {error}", exc_info=error)

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.content.startswith(BOT_PREFIX):
        log.info(f"Command received: {message.content} from {message.author} in {message.guild}")
    await bot.process_commands(message)


async def connect_lavalink():
    node = wavelink.Node(
        uri=f"http://{LAVALINK_HOST}:{LAVALINK_PORT}",
        password=LAVALINK_PASSWORD,
        identifier="gcp-primary",
        inactive_player_timeout=None,
        inactive_channel_tokens=None,
    )
    await wavelink.Pool.connect(client=bot, nodes=[node])
    log.info("Connected to Lavalink (gcp-primary)")


@bot.event
async def setup_hook():
    await connect_lavalink()
    extensions = [
        "cogs.activation",
        "cogs.playback",
        "cogs.queue_cmd",
        "cogs.playlist_cmd",
        "cogs.history_cmd",
        "cogs.session_cmd",
        "cogs.localnode_cmd",
        "cogs.admin_cmd",
    ]
    for ext in extensions:
        try:
            await bot.load_extension(ext)
            log.info(f"Loaded extension: {ext}")
        except Exception as e:
            log.error(f"Failed to load extension {ext}: {e}", exc_info=True)


bot.run(DISCORD_TOKEN)
