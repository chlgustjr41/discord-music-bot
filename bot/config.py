import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")
FIREBASE_SERVICE_ACCOUNT_KEY = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
LAVALINK_HOST = os.getenv("LAVALINK_HOST", "localhost")
LAVALINK_PORT = int(os.getenv("LAVALINK_PORT", "2333"))
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")

BOT_PREFIX = "j!"
EMBED_COLOR = 0x1DB954  # Spotify-green accent
IDLE_TIMEOUT_SECONDS = 300  # 5 minutes
SESSION_CODE_LENGTH = 6
WEB_APP_URL = os.getenv("WEB_APP_URL", "http://localhost:5173")
