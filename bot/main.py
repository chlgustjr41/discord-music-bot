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

@bot.event
async def on_command_error(ctx, error):
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
    )
    await wavelink.Pool.connect(client=bot, nodes=[node])
    log.info("Connected to Lavalink")


@bot.event
async def setup_hook():
    await connect_lavalink()
    await bot.load_extension("cogs.activation")
    await bot.load_extension("cogs.playback")
    await bot.load_extension("cogs.queue_cmd")
    await bot.load_extension("cogs.playlist_cmd")
    await bot.load_extension("cogs.history_cmd")
    await bot.load_extension("cogs.session_cmd")


bot.run(DISCORD_TOKEN)
