# Jacky Music Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Discord music bot ("Jacky Music") with a companion React web app that shares playlist state via Firebase Firestore in real-time.

**Architecture:** Python Discord bot (discord.py + wavelink) connects to a self-hosted Lavalink server for audio streaming. Firebase Firestore is the single source of truth — both the bot and the React web app read/write the same documents. Firebase Auth gates server activation via Google login.

**Tech Stack:** Python 3.11, discord.py, wavelink, firebase-admin SDK, spotipy | React 18, Vite, TypeScript, Firebase JS SDK | Lavalink v4, Docker Compose | Firebase Hosting, Firestore, Auth, Cloud Functions

---

## File Structure

### Bot (`bot/`)
```
bot/
├── Dockerfile
├── requirements.txt
├── main.py                    # Entry point: bot setup, cog loading, Lavalink connect
├── config.py                  # Env vars, constants (prefix, colors, timeouts)
├── cogs/
│   ├── __init__.py
│   ├── activation.py          # Before-invoke check: server must be activated
│   ├── playback.py            # j!play, j!pause, j!resume, j!skip, j!stop, j!volume, j!loop, j!nowplaying
│   ├── queue_cmd.py           # j!queue, j!remove, j!move, j!shuffle
│   ├── playlist_cmd.py        # j!playlist save/load/list/delete
│   ├── history_cmd.py         # j!history
│   └── session_cmd.py         # j!session
├── services/
│   ├── __init__.py
│   ├── firestore_client.py    # All Firestore read/write operations
│   ├── spotify_client.py      # Spotify link detection + track name resolution
│   ├── session_manager.py     # Generate/store/invalidate session codes
│   └── firestore_listener.py  # Listen for web app changes, sync to Lavalink
└── utils/
    ├── __init__.py
    └── embeds.py              # Discord embed builders (now playing, queue, etc.)
```

### Web App (`frontend/`)
```
frontend/
├── index.html
├── package.json
├── tsconfig.json
├── tsconfig.app.json
├── vite.config.ts
├── firebase.json              # Firebase Hosting config
├── .firebaserc                # Firebase project alias
├── src/
│   ├── main.tsx
│   ├── App.tsx                # Router: entry screen vs dashboard
│   ├── firebase.ts            # Firebase app init, auth, firestore exports
│   ├── types.ts               # Shared TS types (Track, ServerState, Playlist, etc.)
│   ├── components/
│   │   ├── EntryScreen.tsx    # Session code input + "Activate Server" button
│   │   ├── ActivateServer.tsx # Google login + Discord server ID form
│   │   ├── Dashboard.tsx      # Main dashboard layout (grid of panels)
│   │   ├── NowPlaying.tsx     # Current track: title, artist, thumbnail, progress
│   │   ├── Queue.tsx          # Ordered track list with drag-to-reorder, remove
│   │   ├── PlaybackControls.tsx # pause/resume, skip, shuffle, loop, volume
│   │   ├── SearchPanel.tsx    # YouTube search input + results + add-to-queue
│   │   ├── PlaylistManager.tsx # Save/load/delete named playlists
│   │   └── HistoryPanel.tsx   # Past sessions with track lists, re-queue
│   ├── hooks/
│   │   ├── useServerState.ts  # Real-time Firestore listener for server doc
│   │   └── useAuth.ts         # Firebase Auth state hook
│   └── services/
│       └── api.ts             # Cloud Function calls (YouTube search)
```

### Cloud Function (`functions/`)
```
functions/
├── package.json
├── tsconfig.json
└── src/
    └── index.ts               # YouTube Data API v3 search proxy
```

### Root
```
docker-compose.yml
lavalink/
└── application.yml            # Lavalink server config
.env.example
.gitignore
README.md
CLAUDE.md
```

---

## Task 1: Project Scaffold & Configuration (GitHub Issue #1)

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `README.md`
- Create: `CLAUDE.md`
- Create: `bot/requirements.txt`
- Create: `bot/config.py`
- Create: `bot/main.py` (skeleton)
- Create: `bot/Dockerfile`
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.app.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx` (skeleton)

- [ ] **Step 1: Create `.gitignore`**

```gitignore
# Python
__pycache__/
*.pyc
.venv/
bot/.env

# Node
node_modules/
dist/
frontend/.env
functions/.env

# Firebase
.firebase/

# IDE
.vscode/
.idea/

# Environment
.env
*.local
```

- [ ] **Step 2: Create `.env.example`**

```env
# Discord
DISCORD_TOKEN=your_discord_bot_token

# Firebase (bot uses service account)
FIREBASE_PROJECT_ID=your_project_id
FIREBASE_SERVICE_ACCOUNT_KEY=path/to/serviceAccountKey.json

# Spotify
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret

# Lavalink
LAVALINK_HOST=localhost
LAVALINK_PORT=2333
LAVALINK_PASSWORD=youshallnotpass

# YouTube Data API (for Cloud Function)
YOUTUBE_API_KEY=your_youtube_api_key
```

- [ ] **Step 3: Create `bot/requirements.txt`**

```txt
discord.py==2.4.0
wavelink==3.4.1
firebase-admin==6.6.0
spotipy==2.24.0
python-dotenv==1.0.1
```

- [ ] **Step 4: Create `bot/config.py`**

```python
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
```

- [ ] **Step 5: Create `bot/main.py` skeleton**

```python
import asyncio
import discord
from discord.ext import commands
import firebase_admin
from firebase_admin import credentials, firestore
import wavelink

from config import (
    DISCORD_TOKEN,
    FIREBASE_SERVICE_ACCOUNT_KEY,
    LAVALINK_HOST,
    LAVALINK_PORT,
    LAVALINK_PASSWORD,
    BOT_PREFIX,
)

# Firebase init
cred = credentials.Certificate(FIREBASE_SERVICE_ACCOUNT_KEY)
firebase_admin.initialize_app(cred)
db = firestore.client()

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents)


@bot.event
async def on_ready():
    print(f"Jacky Music is online as {bot.user}")


async def connect_lavalink():
    node = wavelink.Node(
        uri=f"http://{LAVALINK_HOST}:{LAVALINK_PORT}",
        password=LAVALINK_PASSWORD,
    )
    await wavelink.Pool.connect(client=bot, nodes=[node])
    print("Connected to Lavalink")


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
```

- [ ] **Step 6: Create `bot/Dockerfile`**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
```

- [ ] **Step 7: Scaffold frontend with Vite**

```bash
cd D:/web-project/discord-music-bot
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install firebase react-router-dom
npm install -D @types/react-router-dom
```

- [ ] **Step 8: Create `CLAUDE.md`**

```markdown
# CLAUDE.md

## Project: Jacky Music (Discord Music Bot)

### Repository Structure
- `bot/` — Python Discord bot (discord.py + wavelink)
- `frontend/` — React + Vite + TypeScript web app
- `functions/` — Firebase Cloud Functions (YouTube search proxy)
- `lavalink/` — Lavalink server configuration

### Commands

#### Bot
\`\`\`bash
cd bot
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
python main.py
\`\`\`

#### Frontend
\`\`\`bash
cd frontend
npm install
npm run dev        # Vite dev server (port 5173)
npm run build      # Production build
npm run preview    # Preview production build
\`\`\`

#### Cloud Functions
\`\`\`bash
cd functions
npm install
npm run build
firebase deploy --only functions
\`\`\`

#### Docker (bot + Lavalink)
\`\`\`bash
docker compose up -d --build
\`\`\`

### Architecture
- Firestore is the single source of truth for playlist state
- Bot and web app both read/write the same Firestore documents
- Bot uses firebase-admin SDK (service account)
- Web app uses Firebase JS SDK (client-side)
- Lavalink handles audio extraction and streaming to Discord voice
- Firebase Auth (Google login) gates server activation

### Path Alias
`@/*` maps to `src/*` in the frontend.

### Environment Variables
See `.env.example` for all required variables.
```

- [ ] **Step 9: Commit scaffold**

```bash
git add -A
git commit -m "feat: project scaffold with bot, frontend, and config"
```

---

## Task 2: Firestore Client Service (GitHub Issue #2)

**Files:**
- Create: `bot/services/__init__.py`
- Create: `bot/services/firestore_client.py`
- Create: `bot/tests/__init__.py`
- Create: `bot/tests/test_firestore_client.py`

- [ ] **Step 1: Create `bot/services/__init__.py`**

```python
```

- [ ] **Step 2: Create `bot/tests/__init__.py`**

```python
```

- [ ] **Step 3: Write `bot/services/firestore_client.py`**

This service encapsulates all Firestore operations. The bot imports `db` from `main.py` — but to keep this testable, the client accepts a `db` instance.

```python
from google.cloud.firestore_v1 import DocumentReference
from firebase_admin import firestore
from typing import Optional
import time


class FirestoreClient:
    def __init__(self, db):
        self.db = db

    # --- Server Activation ---

    def is_server_activated(self, server_id: str) -> bool:
        doc = self.db.collection("serverOwners").document(str(server_id)).get()
        return doc.exists and doc.to_dict().get("isActive", False)

    # --- Server State ---

    def get_server_state(self, server_id: str) -> Optional[dict]:
        doc = self.db.collection("servers").document(str(server_id)).get()
        return doc.to_dict() if doc.exists else None

    def update_server_state(self, server_id: str, data: dict):
        self.db.collection("servers").document(str(server_id)).set(data, merge=True)

    def init_server_state(self, server_id: str):
        ref = self.db.collection("servers").document(str(server_id))
        if not ref.get().exists:
            ref.set({
                "sessionCode": None,
                "currentTrack": None,
                "queue": [],
                "isPlaying": False,
                "isPaused": False,
                "loopMode": "off",
                "volume": 80,
                "voiceChannelId": None,
                "textChannelId": None,
                "idleTimeoutMinutes": 5,
            })

    # --- Queue Operations ---

    def get_queue(self, server_id: str) -> list:
        state = self.get_server_state(server_id)
        return state.get("queue", []) if state else []

    def add_to_queue(self, server_id: str, track: dict):
        self.db.collection("servers").document(str(server_id)).update({
            "queue": firestore.ArrayUnion([track])
        })

    def remove_from_queue(self, server_id: str, index: int):
        state = self.get_server_state(server_id)
        if state:
            queue = state.get("queue", [])
            if 0 <= index < len(queue):
                queue.pop(index)
                self.update_server_state(server_id, {"queue": queue})

    def clear_queue(self, server_id: str):
        self.update_server_state(server_id, {"queue": [], "currentTrack": None})

    def reorder_queue(self, server_id: str, from_idx: int, to_idx: int):
        state = self.get_server_state(server_id)
        if state:
            queue = state.get("queue", [])
            if 0 <= from_idx < len(queue) and 0 <= to_idx < len(queue):
                track = queue.pop(from_idx)
                queue.insert(to_idx, track)
                self.update_server_state(server_id, {"queue": queue})

    def shuffle_queue(self, server_id: str):
        import random
        state = self.get_server_state(server_id)
        if state:
            queue = state.get("queue", [])
            random.shuffle(queue)
            self.update_server_state(server_id, {"queue": queue})

    # --- Current Track ---

    def set_current_track(self, server_id: str, track: Optional[dict]):
        data = {"currentTrack": track, "isPlaying": track is not None, "isPaused": False}
        self.update_server_state(server_id, data)

    def pop_next_track(self, server_id: str) -> Optional[dict]:
        state = self.get_server_state(server_id)
        if not state:
            return None
        queue = state.get("queue", [])
        if not queue:
            return None
        track = queue.pop(0)
        self.update_server_state(server_id, {"queue": queue})
        return track

    # --- Session Codes ---

    def set_session_code(self, server_id: str, code: str):
        self.update_server_state(server_id, {"sessionCode": code})
        self.db.collection("sessionCodes").document(code).set({
            "serverId": str(server_id),
            "createdAt": firestore.SERVER_TIMESTAMP,
        })

    def invalidate_session_code(self, server_id: str):
        state = self.get_server_state(server_id)
        if state and state.get("sessionCode"):
            self.db.collection("sessionCodes").document(state["sessionCode"]).delete()
            self.update_server_state(server_id, {"sessionCode": None})

    def resolve_session_code(self, code: str) -> Optional[str]:
        doc = self.db.collection("sessionCodes").document(code).get()
        return doc.to_dict().get("serverId") if doc.exists else None

    # --- Playlists ---

    def save_playlist(self, server_id: str, name: str, tracks: list, created_by: str):
        self.db.collection("servers").document(str(server_id)).collection("playlists").document(name).set({
            "name": name,
            "tracks": tracks,
            "createdBy": created_by,
            "createdAt": firestore.SERVER_TIMESTAMP,
        })

    def load_playlist(self, server_id: str, name: str) -> Optional[dict]:
        doc = (self.db.collection("servers").document(str(server_id))
               .collection("playlists").document(name).get())
        return doc.to_dict() if doc.exists else None

    def list_playlists(self, server_id: str) -> list:
        docs = (self.db.collection("servers").document(str(server_id))
                .collection("playlists").stream())
        return [{"name": d.id, **d.to_dict()} for d in docs]

    def delete_playlist(self, server_id: str, name: str):
        (self.db.collection("servers").document(str(server_id))
         .collection("playlists").document(name).delete())

    # --- History ---

    def save_history(self, server_id: str, session_id: str, tracks: list,
                     started_at, ended_at):
        self.db.collection("servers").document(str(server_id)).collection("history").document(session_id).set({
            "startedAt": started_at,
            "endedAt": ended_at,
            "tracks": tracks,
        })

    def get_history(self, server_id: str, limit: int = 10) -> list:
        docs = (self.db.collection("servers").document(str(server_id))
                .collection("history")
                .order_by("startedAt", direction=firestore.Query.DESCENDING)
                .limit(limit).stream())
        return [{"id": d.id, **d.to_dict()} for d in docs]
```

- [ ] **Step 4: Commit**

```bash
git add bot/services/ bot/tests/
git commit -m "feat: add Firestore client service with all CRUD operations"
```

---

## Task 3: Session Manager & Embed Utilities (GitHub Issue #3)

**Files:**
- Create: `bot/services/session_manager.py`
- Create: `bot/utils/__init__.py`
- Create: `bot/utils/embeds.py`
- Create: `bot/cogs/__init__.py`

- [ ] **Step 1: Write `bot/services/session_manager.py`**

```python
import random
import string
from config import SESSION_CODE_LENGTH


def generate_session_code(length: int = SESSION_CODE_LENGTH) -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=length))
```

- [ ] **Step 2: Write `bot/utils/__init__.py` and `bot/cogs/__init__.py`**

Both empty `__init__.py` files.

- [ ] **Step 3: Write `bot/utils/embeds.py`**

```python
import discord
from config import EMBED_COLOR


def now_playing_embed(track: dict) -> discord.Embed:
    embed = discord.Embed(
        title="Now Playing",
        description=f"**{track['title']}**\n{track.get('artist', 'Unknown')}",
        color=EMBED_COLOR,
    )
    if track.get("thumbnail"):
        embed.set_thumbnail(url=track["thumbnail"])
    duration = track.get("duration", 0)
    minutes, seconds = divmod(duration, 60)
    embed.add_field(name="Duration", value=f"{minutes}:{seconds:02d}")
    if track.get("requestedBy"):
        embed.set_footer(text=f"Requested by {track['requestedBy']}")
    return embed


def queue_embed(queue: list, current_track: dict = None, page: int = 0, per_page: int = 10) -> discord.Embed:
    embed = discord.Embed(title="Queue", color=EMBED_COLOR)
    if current_track:
        embed.add_field(
            name="Now Playing",
            value=f"**{current_track['title']}** — {current_track.get('artist', 'Unknown')}",
            inline=False,
        )
    if not queue:
        embed.description = "Queue is empty."
        return embed
    start = page * per_page
    end = start + per_page
    lines = []
    for i, track in enumerate(queue[start:end], start=start + 1):
        duration = track.get("duration", 0)
        minutes, seconds = divmod(duration, 60)
        lines.append(f"`{i}.` **{track['title']}** — {minutes}:{seconds:02d}")
    embed.description = "\n".join(lines)
    total_pages = (len(queue) - 1) // per_page + 1
    embed.set_footer(text=f"Page {page + 1}/{total_pages} | {len(queue)} tracks")
    return embed


def session_embed(code: str, web_url: str) -> discord.Embed:
    return discord.Embed(
        title="Session Code",
        description=f"**`{code}`**\n\nVisit [{web_url}]({web_url}) and enter this code to manage the playlist from your browser.",
        color=EMBED_COLOR,
    )


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(
        title="Error",
        description=message,
        color=0xFF0000,
    )


def success_embed(message: str) -> discord.Embed:
    return discord.Embed(
        description=message,
        color=EMBED_COLOR,
    )
```

- [ ] **Step 4: Commit**

```bash
git add bot/services/session_manager.py bot/utils/ bot/cogs/__init__.py
git commit -m "feat: add session code generator and Discord embed utilities"
```

---

## Task 4: Server Activation Check Cog (GitHub Issue #4)

**Files:**
- Create: `bot/cogs/activation.py`

- [ ] **Step 1: Write `bot/cogs/activation.py`**

This cog adds a global before-invoke hook. Every command checks if the server is activated.

```python
import discord
from discord.ext import commands
from config import WEB_APP_URL
from utils.embeds import error_embed


class Activation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs  # FirestoreClient set on bot in main.py

    async def cog_check(self, ctx: commands.Context) -> bool:
        """Global check — runs before every command in every cog."""
        return True  # This cog's own commands don't need the check

    @commands.Cog.listener()
    async def on_command(self, ctx: commands.Context):
        pass  # Logging hook if needed later


class ActivationCheck(commands.Cog):
    """Registers a bot-wide check for server activation."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs
        bot.add_check(self.global_activation_check)

    async def global_activation_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            await ctx.send(embed=error_embed("Commands only work in a server."))
            return False
        if not self.fs.is_server_activated(str(ctx.guild.id)):
            await ctx.send(embed=error_embed(
                f"This server has not been activated.\n"
                f"The server owner must visit [{WEB_APP_URL}]({WEB_APP_URL}) "
                f"and sign in with Google to activate Jacky Music."
            ))
            return False
        return True

    def cog_unload(self):
        self.bot.remove_check(self.global_activation_check)


async def setup(bot: commands.Bot):
    await bot.add_cog(ActivationCheck(bot))
```

- [ ] **Step 2: Update `bot/main.py` to attach `FirestoreClient` to bot**

Add after firebase init, before `bot.run()`:

```python
from services.firestore_client import FirestoreClient

# ... existing firebase init ...

bot.fs = FirestoreClient(db)
```

- [ ] **Step 3: Commit**

```bash
git add bot/cogs/activation.py bot/main.py
git commit -m "feat: add server activation check — bot only responds in activated servers"
```

---

## Task 5: Playback Cog — Voice Join, Play, Pause, Resume, Skip, Stop (GitHub Issue #5)

**Files:**
- Create: `bot/cogs/playback.py`

- [ ] **Step 1: Write `bot/cogs/playback.py`**

```python
import asyncio
import datetime
import discord
import wavelink
from discord.ext import commands
from config import IDLE_TIMEOUT_SECONDS, WEB_APP_URL
from services.session_manager import generate_session_code
from utils.embeds import now_playing_embed, session_embed, error_embed, success_embed


class Playback(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs
        self.idle_tasks: dict[int, asyncio.Task] = {}
        self.history_buffer: dict[int, list] = {}  # server_id -> played tracks
        self.session_start: dict[int, datetime.datetime] = {}

    async def ensure_voice(self, ctx: commands.Context) -> wavelink.Player | None:
        if not ctx.author.voice:
            await ctx.send(embed=error_embed("You must be in a voice channel."))
            return None
        player = ctx.voice_client
        if not player:
            player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
            player.autoplay = wavelink.AutoPlayMode.disabled
            # Generate session code
            code = generate_session_code()
            self.fs.set_session_code(str(ctx.guild.id), code)
            self.fs.update_server_state(str(ctx.guild.id), {
                "voiceChannelId": str(ctx.author.voice.channel.id),
                "textChannelId": str(ctx.channel.id),
            })
            await ctx.send(embed=session_embed(code, WEB_APP_URL))
            # Init history buffer
            self.history_buffer[ctx.guild.id] = []
            self.session_start[ctx.guild.id] = datetime.datetime.now()
        return player

    async def play_next(self, player: wavelink.Player, guild_id: int):
        track_data = self.fs.pop_next_track(str(guild_id))
        if not track_data:
            self.fs.set_current_track(str(guild_id), None)
            self.fs.update_server_state(str(guild_id), {"isPlaying": False})
            self.start_idle_timer(guild_id, player)
            return

        results = await wavelink.Playable.search(track_data["url"])
        if not results:
            results = await wavelink.Playable.search(f"{track_data['title']} {track_data.get('artist', '')}")
        if not results:
            text_channel_id = self.fs.get_server_state(str(guild_id)).get("textChannelId")
            if text_channel_id:
                channel = self.bot.get_channel(int(text_channel_id))
                if channel:
                    await channel.send(embed=error_embed(f"Could not find: {track_data['title']}"))
            await self.play_next(player, guild_id)
            return

        playable = results[0] if isinstance(results, list) else results
        track_data["startedAt"] = datetime.datetime.now().isoformat()
        track_data["duration"] = playable.length // 1000
        self.fs.set_current_track(str(guild_id), track_data)

        # Add to history buffer
        if guild_id in self.history_buffer:
            self.history_buffer[guild_id].append({
                **track_data,
                "playedAt": datetime.datetime.now().isoformat(),
            })

        await player.play(playable)
        self.cancel_idle_timer(guild_id)

    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player = payload.player
        if not player or not player.guild:
            return
        guild_id = player.guild.id
        state = self.fs.get_server_state(str(guild_id))
        if not state:
            return

        loop_mode = state.get("loopMode", "off")
        if loop_mode == "track" and state.get("currentTrack"):
            # Re-play current track
            current = state["currentTrack"]
            results = await wavelink.Playable.search(current["url"])
            if results:
                playable = results[0] if isinstance(results, list) else results
                await player.play(playable)
                return
        elif loop_mode == "queue" and state.get("currentTrack"):
            # Add current track back to end of queue
            current = state["currentTrack"]
            self.fs.add_to_queue(str(guild_id), {
                "title": current["title"],
                "artist": current.get("artist", ""),
                "url": current["url"],
                "thumbnail": current.get("thumbnail", ""),
                "duration": current.get("duration", 0),
                "requestedBy": current.get("requestedBy", ""),
            })

        await self.play_next(player, guild_id)

    @commands.command(name="play", aliases=["p"])
    async def play(self, ctx: commands.Context, *, query: str):
        player = await self.ensure_voice(ctx)
        if not player:
            return

        # Spotify link handling delegated to spotify_client (Task 8)
        # For now, search YouTube via Lavalink
        results = await wavelink.Playable.search(query)
        if not results:
            await ctx.send(embed=error_embed(f"No results found for: {query}"))
            return

        playable = results[0] if isinstance(results, list) else results
        track_data = {
            "title": playable.title,
            "artist": playable.author,
            "url": playable.uri or query,
            "thumbnail": getattr(playable, "artwork", "") or "",
            "duration": playable.length // 1000,
            "requestedBy": ctx.author.display_name,
        }

        if player.playing:
            self.fs.add_to_queue(str(ctx.guild.id), track_data)
            await ctx.send(embed=success_embed(
                f"Added to queue: **{playable.title}** — {playable.author}"
            ))
        else:
            self.fs.set_current_track(str(ctx.guild.id), {
                **track_data,
                "startedAt": datetime.datetime.now().isoformat(),
            })
            if ctx.guild.id in self.history_buffer:
                self.history_buffer[ctx.guild.id].append({
                    **track_data,
                    "playedAt": datetime.datetime.now().isoformat(),
                })
            await player.play(playable)
            await ctx.send(embed=now_playing_embed(track_data))
            self.cancel_idle_timer(ctx.guild.id)

    @commands.command(name="pause")
    async def pause(self, ctx: commands.Context):
        player = ctx.voice_client
        if player and player.playing:
            await player.pause(True)
            self.fs.update_server_state(str(ctx.guild.id), {"isPaused": True})
            await ctx.send(embed=success_embed("Paused."))

    @commands.command(name="resume", aliases=["unpause"])
    async def resume(self, ctx: commands.Context):
        player = ctx.voice_client
        if player and player.paused:
            await player.pause(False)
            self.fs.update_server_state(str(ctx.guild.id), {"isPaused": False})
            await ctx.send(embed=success_embed("Resumed."))

    @commands.command(name="skip", aliases=["s"])
    async def skip(self, ctx: commands.Context):
        player = ctx.voice_client
        if player and player.playing:
            await player.stop()
            await ctx.send(embed=success_embed("Skipped."))

    @commands.command(name="stop", aliases=["leave", "disconnect", "dc"])
    async def stop(self, ctx: commands.Context):
        player = ctx.voice_client
        if player:
            guild_id = ctx.guild.id
            # Save history
            await self.save_session_history(guild_id)
            # Clean up
            self.fs.clear_queue(str(guild_id))
            self.fs.invalidate_session_code(str(guild_id))
            self.fs.update_server_state(str(guild_id), {
                "isPlaying": False,
                "isPaused": False,
                "voiceChannelId": None,
                "textChannelId": None,
            })
            self.cancel_idle_timer(guild_id)
            await player.disconnect()
            await ctx.send(embed=success_embed("Disconnected. Session ended."))

    @commands.command(name="volume", aliases=["vol"])
    async def volume(self, ctx: commands.Context, vol: int):
        player = ctx.voice_client
        if not player:
            await ctx.send(embed=error_embed("Not connected to voice."))
            return
        vol = max(0, min(100, vol))
        await player.set_volume(vol)
        self.fs.update_server_state(str(ctx.guild.id), {"volume": vol})
        await ctx.send(embed=success_embed(f"Volume set to **{vol}%**"))

    @commands.command(name="loop")
    async def loop(self, ctx: commands.Context):
        state = self.fs.get_server_state(str(ctx.guild.id))
        current = state.get("loopMode", "off") if state else "off"
        cycle = {"off": "track", "track": "queue", "queue": "off"}
        new_mode = cycle[current]
        self.fs.update_server_state(str(ctx.guild.id), {"loopMode": new_mode})
        labels = {"off": "Loop off", "track": "Looping current track", "queue": "Looping queue"}
        await ctx.send(embed=success_embed(labels[new_mode]))

    @commands.command(name="nowplaying", aliases=["np"])
    async def nowplaying(self, ctx: commands.Context):
        state = self.fs.get_server_state(str(ctx.guild.id))
        if state and state.get("currentTrack"):
            await ctx.send(embed=now_playing_embed(state["currentTrack"]))
        else:
            await ctx.send(embed=error_embed("Nothing is playing."))

    # --- Idle Timer ---

    def start_idle_timer(self, guild_id: int, player: wavelink.Player):
        self.cancel_idle_timer(guild_id)
        self.idle_tasks[guild_id] = asyncio.create_task(
            self._idle_disconnect(guild_id, player)
        )

    def cancel_idle_timer(self, guild_id: int):
        task = self.idle_tasks.pop(guild_id, None)
        if task:
            task.cancel()

    async def _idle_disconnect(self, guild_id: int, player: wavelink.Player):
        await asyncio.sleep(IDLE_TIMEOUT_SECONDS)
        if player.connected and not player.playing:
            await self.save_session_history(guild_id)
            self.fs.invalidate_session_code(str(guild_id))
            self.fs.update_server_state(str(guild_id), {
                "isPlaying": False,
                "isPaused": False,
                "voiceChannelId": None,
                "textChannelId": None,
            })
            text_channel_id = self.fs.get_server_state(str(guild_id)).get("textChannelId")
            await player.disconnect()
            if text_channel_id:
                channel = self.bot.get_channel(int(text_channel_id))
                if channel:
                    await channel.send(embed=success_embed(
                        "Disconnected due to inactivity. Session ended."
                    ))

    async def save_session_history(self, guild_id: int):
        tracks = self.history_buffer.pop(guild_id, [])
        started = self.session_start.pop(guild_id, None)
        if tracks and started:
            session_id = started.strftime("%Y%m%d-%H%M%S")
            self.fs.save_history(
                str(guild_id), session_id, tracks,
                started.isoformat(), datetime.datetime.now().isoformat()
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Playback(bot))
```

- [ ] **Step 2: Commit**

```bash
git add bot/cogs/playback.py
git commit -m "feat: add playback cog — play, pause, resume, skip, stop, volume, loop, idle timeout"
```

---

## Task 6: Queue Management Cog (GitHub Issue #6)

**Files:**
- Create: `bot/cogs/queue_cmd.py`

- [ ] **Step 1: Write `bot/cogs/queue_cmd.py`**

```python
from discord.ext import commands
from utils.embeds import queue_embed, error_embed, success_embed


class QueueCmd(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs

    @commands.command(name="queue", aliases=["q"])
    async def queue(self, ctx: commands.Context, page: int = 1):
        state = self.fs.get_server_state(str(ctx.guild.id))
        if not state:
            await ctx.send(embed=error_embed("No active session."))
            return
        q = state.get("queue", [])
        current = state.get("currentTrack")
        embed = queue_embed(q, current, page=page - 1)
        await ctx.send(embed=embed)

    @commands.command(name="remove")
    async def remove(self, ctx: commands.Context, position: int):
        queue = self.fs.get_queue(str(ctx.guild.id))
        if position < 1 or position > len(queue):
            await ctx.send(embed=error_embed(f"Invalid position. Queue has {len(queue)} tracks."))
            return
        removed = queue[position - 1]
        self.fs.remove_from_queue(str(ctx.guild.id), position - 1)
        await ctx.send(embed=success_embed(f"Removed: **{removed['title']}**"))

    @commands.command(name="move")
    async def move(self, ctx: commands.Context, from_pos: int, to_pos: int):
        queue = self.fs.get_queue(str(ctx.guild.id))
        if (from_pos < 1 or from_pos > len(queue) or
                to_pos < 1 or to_pos > len(queue)):
            await ctx.send(embed=error_embed(f"Invalid positions. Queue has {len(queue)} tracks."))
            return
        track = queue[from_pos - 1]
        self.fs.reorder_queue(str(ctx.guild.id), from_pos - 1, to_pos - 1)
        await ctx.send(embed=success_embed(
            f"Moved **{track['title']}** from position {from_pos} to {to_pos}"
        ))

    @commands.command(name="shuffle")
    async def shuffle(self, ctx: commands.Context):
        queue = self.fs.get_queue(str(ctx.guild.id))
        if not queue:
            await ctx.send(embed=error_embed("Queue is empty."))
            return
        self.fs.shuffle_queue(str(ctx.guild.id))
        await ctx.send(embed=success_embed(f"Shuffled {len(queue)} tracks."))


async def setup(bot: commands.Bot):
    await bot.add_cog(QueueCmd(bot))
```

- [ ] **Step 2: Commit**

```bash
git add bot/cogs/queue_cmd.py
git commit -m "feat: add queue management cog — queue, remove, move, shuffle"
```

---

## Task 7: Playlist & History Cogs (GitHub Issue #7)

**Files:**
- Create: `bot/cogs/playlist_cmd.py`
- Create: `bot/cogs/history_cmd.py`
- Create: `bot/cogs/session_cmd.py`

- [ ] **Step 1: Write `bot/cogs/playlist_cmd.py`**

```python
from discord.ext import commands
from utils.embeds import error_embed, success_embed
from config import EMBED_COLOR
import discord


class PlaylistCmd(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs

    @commands.group(name="playlist", aliases=["pl"], invoke_without_command=True)
    async def playlist(self, ctx: commands.Context):
        await ctx.send(embed=error_embed(
            "Usage: `j!playlist save <name>`, `j!playlist load <name>`, "
            "`j!playlist list`, `j!playlist delete <name>`"
        ))

    @playlist.command(name="save")
    async def save(self, ctx: commands.Context, *, name: str):
        state = self.fs.get_server_state(str(ctx.guild.id))
        if not state:
            await ctx.send(embed=error_embed("No active session."))
            return
        queue = state.get("queue", [])
        current = state.get("currentTrack")
        tracks = []
        if current:
            tracks.append({
                "title": current["title"],
                "artist": current.get("artist", ""),
                "url": current["url"],
                "thumbnail": current.get("thumbnail", ""),
                "duration": current.get("duration", 0),
            })
        tracks.extend([{
            "title": t["title"],
            "artist": t.get("artist", ""),
            "url": t["url"],
            "thumbnail": t.get("thumbnail", ""),
            "duration": t.get("duration", 0),
        } for t in queue])
        if not tracks:
            await ctx.send(embed=error_embed("Nothing to save — queue is empty."))
            return
        self.fs.save_playlist(str(ctx.guild.id), name, tracks, ctx.author.display_name)
        await ctx.send(embed=success_embed(f"Saved playlist **{name}** with {len(tracks)} tracks."))

    @playlist.command(name="load")
    async def load(self, ctx: commands.Context, *, name: str):
        playlist_data = self.fs.load_playlist(str(ctx.guild.id), name)
        if not playlist_data:
            await ctx.send(embed=error_embed(f"Playlist **{name}** not found."))
            return
        tracks = playlist_data.get("tracks", [])
        for track in tracks:
            track["requestedBy"] = ctx.author.display_name
            self.fs.add_to_queue(str(ctx.guild.id), track)
        await ctx.send(embed=success_embed(
            f"Loaded **{len(tracks)}** tracks from playlist **{name}** into queue."
        ))

    @playlist.command(name="list", aliases=["ls"])
    async def list_playlists(self, ctx: commands.Context):
        playlists = self.fs.list_playlists(str(ctx.guild.id))
        if not playlists:
            await ctx.send(embed=error_embed("No saved playlists."))
            return
        embed = discord.Embed(title="Saved Playlists", color=EMBED_COLOR)
        lines = []
        for p in playlists:
            count = len(p.get("tracks", []))
            lines.append(f"**{p['name']}** — {count} tracks (by {p.get('createdBy', '?')})")
        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    @playlist.command(name="delete", aliases=["del", "rm"])
    async def delete(self, ctx: commands.Context, *, name: str):
        existing = self.fs.load_playlist(str(ctx.guild.id), name)
        if not existing:
            await ctx.send(embed=error_embed(f"Playlist **{name}** not found."))
            return
        self.fs.delete_playlist(str(ctx.guild.id), name)
        await ctx.send(embed=success_embed(f"Deleted playlist **{name}**."))


async def setup(bot: commands.Bot):
    await bot.add_cog(PlaylistCmd(bot))
```

- [ ] **Step 2: Write `bot/cogs/history_cmd.py`**

```python
import discord
from discord.ext import commands
from utils.embeds import error_embed
from config import EMBED_COLOR


class HistoryCmd(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs

    @commands.command(name="history")
    async def history(self, ctx: commands.Context):
        sessions = self.fs.get_history(str(ctx.guild.id), limit=5)
        if not sessions:
            await ctx.send(embed=error_embed("No play history yet."))
            return
        embed = discord.Embed(title="Recent Sessions", color=EMBED_COLOR)
        for session in sessions:
            tracks = session.get("tracks", [])
            track_list = "\n".join(
                f"  {i+1}. {t['title']}" for i, t in enumerate(tracks[:5])
            )
            if len(tracks) > 5:
                track_list += f"\n  ... and {len(tracks) - 5} more"
            started = session.get("startedAt", "?")
            embed.add_field(
                name=f"Session {session['id']} ({started[:10]})",
                value=track_list or "No tracks",
                inline=False,
            )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(HistoryCmd(bot))
```

- [ ] **Step 3: Write `bot/cogs/session_cmd.py`**

```python
from discord.ext import commands
from utils.embeds import session_embed, error_embed
from config import WEB_APP_URL


class SessionCmd(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fs = bot.fs

    @commands.command(name="session")
    async def session(self, ctx: commands.Context):
        state = self.fs.get_server_state(str(ctx.guild.id))
        if not state or not state.get("sessionCode"):
            await ctx.send(embed=error_embed(
                "No active session. Use `j!play` to start one."
            ))
            return
        await ctx.send(embed=session_embed(state["sessionCode"], WEB_APP_URL))


async def setup(bot: commands.Bot):
    await bot.add_cog(SessionCmd(bot))
```

- [ ] **Step 4: Commit**

```bash
git add bot/cogs/playlist_cmd.py bot/cogs/history_cmd.py bot/cogs/session_cmd.py
git commit -m "feat: add playlist, history, and session cogs"
```

---

## Task 8: Spotify Link Resolution (GitHub Issue #8)

**Files:**
- Create: `bot/services/spotify_client.py`
- Modify: `bot/cogs/playback.py` (update `play` command)

- [ ] **Step 1: Write `bot/services/spotify_client.py`**

```python
import re
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

SPOTIFY_URL_PATTERN = re.compile(
    r"https?://open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)"
)

sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
))


def is_spotify_url(query: str) -> bool:
    return bool(SPOTIFY_URL_PATTERN.match(query))


def resolve_spotify_url(url: str) -> list[dict]:
    """Resolve a Spotify URL to a list of track dicts with title and artist."""
    match = SPOTIFY_URL_PATTERN.match(url)
    if not match:
        return []

    url_type, spotify_id = match.group(1), match.group(2)

    if url_type == "track":
        track = sp.track(spotify_id)
        return [_track_to_dict(track)]

    elif url_type == "album":
        album = sp.album_tracks(spotify_id)
        return [_track_to_dict(t) for t in album["items"]]

    elif url_type == "playlist":
        results = sp.playlist_tracks(spotify_id)
        tracks = []
        for item in results["items"]:
            if item["track"]:
                tracks.append(_track_to_dict(item["track"]))
        return tracks

    return []


def _track_to_dict(track: dict) -> dict:
    artists = ", ".join(a["name"] for a in track.get("artists", []))
    return {
        "title": track.get("name", "Unknown"),
        "artist": artists,
        "searchQuery": f"{track.get('name', '')} {artists}",
        "thumbnail": (track.get("album", {}).get("images", [{}])[0].get("url", "")
                       if "album" in track else ""),
        "duration": track.get("duration_ms", 0) // 1000,
    }
```

- [ ] **Step 2: Update `bot/cogs/playback.py` `play` command to handle Spotify links**

Replace the `play` command body with Spotify-aware logic. At the top of the file, add:

```python
from services.spotify_client import is_spotify_url, resolve_spotify_url
```

Update the `play` method to check for Spotify URLs first:

```python
    @commands.command(name="play", aliases=["p"])
    async def play(self, ctx: commands.Context, *, query: str):
        player = await self.ensure_voice(ctx)
        if not player:
            return

        # Handle Spotify URLs
        if is_spotify_url(query):
            tracks = resolve_spotify_url(query)
            if not tracks:
                await ctx.send(embed=error_embed("Could not resolve Spotify link."))
                return
            if len(tracks) == 1:
                # Single track — search YouTube and play/queue
                query = tracks[0]["searchQuery"]
                # Fall through to normal YouTube search below
            else:
                # Multiple tracks (album/playlist) — queue all
                for t in tracks:
                    t["requestedBy"] = ctx.author.display_name
                    t["url"] = ""  # Will be resolved when played
                    self.fs.add_to_queue(str(ctx.guild.id), t)
                await ctx.send(embed=success_embed(
                    f"Added **{len(tracks)}** tracks from Spotify to the queue."
                ))
                # Start playing if not already
                if not player.playing:
                    await self.play_next(player, ctx.guild.id)
                return

        # YouTube search via Lavalink
        results = await wavelink.Playable.search(query)
        if not results:
            await ctx.send(embed=error_embed(f"No results found for: {query}"))
            return

        playable = results[0] if isinstance(results, list) else results
        track_data = {
            "title": playable.title,
            "artist": playable.author,
            "url": playable.uri or query,
            "thumbnail": getattr(playable, "artwork", "") or "",
            "duration": playable.length // 1000,
            "requestedBy": ctx.author.display_name,
        }

        if player.playing:
            self.fs.add_to_queue(str(ctx.guild.id), track_data)
            await ctx.send(embed=success_embed(
                f"Added to queue: **{playable.title}** — {playable.author}"
            ))
        else:
            self.fs.set_current_track(str(ctx.guild.id), {
                **track_data,
                "startedAt": datetime.datetime.now().isoformat(),
            })
            if ctx.guild.id in self.history_buffer:
                self.history_buffer[ctx.guild.id].append({
                    **track_data,
                    "playedAt": datetime.datetime.now().isoformat(),
                })
            await player.play(playable)
            await ctx.send(embed=now_playing_embed(track_data))
            self.cancel_idle_timer(ctx.guild.id)
```

- [ ] **Step 3: Commit**

```bash
git add bot/services/spotify_client.py bot/cogs/playback.py
git commit -m "feat: add Spotify link resolution — tracks, albums, playlists"
```

---

## Task 9: Firestore Listener — Web App Changes Sync to Bot (GitHub Issue #9)

**Files:**
- Create: `bot/services/firestore_listener.py`
- Modify: `bot/cogs/playback.py` (start listener on voice join)

- [ ] **Step 1: Write `bot/services/firestore_listener.py`**

```python
import asyncio
from google.cloud.firestore_v1.watch import DocumentChange


class FirestoreListener:
    """Watches a server's Firestore doc for changes from the web app."""

    def __init__(self, bot, fs, server_id: str):
        self.bot = bot
        self.fs = fs
        self.server_id = server_id
        self._unsubscribe = None
        self._last_state = None

    def start(self):
        doc_ref = self.fs.db.collection("servers").document(self.server_id)
        self._unsubscribe = doc_ref.on_snapshot(self._on_snapshot)

    def stop(self):
        if self._unsubscribe:
            self._unsubscribe.unsubscribe()
            self._unsubscribe = None

    def _on_snapshot(self, doc_snapshot, changes, read_time):
        for doc in doc_snapshot:
            new_state = doc.to_dict()
            if self._last_state is None:
                self._last_state = new_state
                continue

            old = self._last_state
            self._last_state = new_state

            # Detect web-triggered changes and sync to player
            asyncio.run_coroutine_threadsafe(
                self._handle_changes(old, new_state),
                self.bot.loop,
            )

    async def _handle_changes(self, old: dict, new: dict):
        guild = self.bot.get_guild(int(self.server_id))
        if not guild:
            return
        player = guild.voice_client
        if not player:
            return

        # Volume change from web
        if old.get("volume") != new.get("volume"):
            await player.set_volume(new.get("volume", 80))

        # Pause/resume from web
        if old.get("isPaused") != new.get("isPaused"):
            await player.pause(new.get("isPaused", False))

        # Skip (web sets currentTrack to None while isPlaying is True)
        if (old.get("currentTrack") is not None and
                new.get("currentTrack") is None and
                new.get("isPlaying", False)):
            await player.stop()  # triggers on_wavelink_track_end -> play_next

        # Shuffle triggered from web (detected by queue order change)
        # No action needed — queue is read from Firestore on next play_next call

        # Loop mode change — no player action needed, read on track end
```

- [ ] **Step 2: Update `bot/cogs/playback.py` to start/stop listener**

Add to `ensure_voice` (after connecting to voice):

```python
from services.firestore_listener import FirestoreListener
```

In the `Playback.__init__`, add:
```python
self.listeners: dict[int, FirestoreListener] = {}
```

In `ensure_voice`, after connecting and setting session code:
```python
listener = FirestoreListener(self.bot, self.fs, str(ctx.guild.id))
listener.start()
self.listeners[ctx.guild.id] = listener
```

In `stop` command and `_idle_disconnect`, before disconnecting:
```python
listener = self.listeners.pop(guild_id, None)
if listener:
    listener.stop()
```

- [ ] **Step 3: Commit**

```bash
git add bot/services/firestore_listener.py bot/cogs/playback.py
git commit -m "feat: add Firestore listener — sync web app changes to bot player"
```

---

## Task 10: Firebase & Frontend Scaffold (GitHub Issue #10)

**Files:**
- Create: `frontend/src/firebase.ts`
- Create: `frontend/src/types.ts`
- Create: `frontend/.env.example`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Install Firebase and routing dependencies**

```bash
cd D:/web-project/discord-music-bot/frontend
npm install firebase react-router-dom
```

- [ ] **Step 2: Create `frontend/.env.example`**

```env
VITE_FIREBASE_API_KEY=your_api_key
VITE_FIREBASE_AUTH_DOMAIN=your_project.firebaseapp.com
VITE_FIREBASE_PROJECT_ID=your_project_id
VITE_FIREBASE_STORAGE_BUCKET=your_project.appspot.com
VITE_FIREBASE_MESSAGING_SENDER_ID=your_sender_id
VITE_FIREBASE_APP_ID=your_app_id
VITE_FIREBASE_DATABASE_URL=https://your_project.firebaseio.com
```

- [ ] **Step 3: Create `frontend/src/firebase.ts`**

```typescript
import { initializeApp } from "firebase/app";
import { getAuth, GoogleAuthProvider } from "firebase/auth";
import { getFirestore } from "firebase/firestore";

const firebaseConfig = {
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
};

const app = initializeApp(firebaseConfig);

export const auth = getAuth(app);
export const googleProvider = new GoogleAuthProvider();
export const db = getFirestore(app);
```

- [ ] **Step 4: Create `frontend/src/types.ts`**

```typescript
export interface Track {
  title: string;
  artist: string;
  url: string;
  thumbnail: string;
  duration: number;
  requestedBy: string;
}

export interface CurrentTrack extends Track {
  startedAt: string;
}

export interface ServerState {
  sessionCode: string | null;
  currentTrack: CurrentTrack | null;
  queue: Track[];
  isPlaying: boolean;
  isPaused: boolean;
  loopMode: "off" | "track" | "queue";
  volume: number;
  voiceChannelId: string | null;
  textChannelId: string | null;
  idleTimeoutMinutes: number;
}

export interface Playlist {
  name: string;
  tracks: Track[];
  createdBy: string;
  createdAt: string;
}

export interface HistorySession {
  id: string;
  startedAt: string;
  endedAt: string;
  tracks: (Track & { playedAt: string })[];
}

export interface SessionCodeDoc {
  serverId: string;
  createdAt: string;
}
```

- [ ] **Step 5: Create `frontend/src/App.tsx`**

```tsx
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { EntryScreen } from "./components/EntryScreen";
import { Dashboard } from "./components/Dashboard";
import { ActivateServer } from "./components/ActivateServer";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<EntryScreen />} />
        <Route path="/dashboard/:sessionCode" element={<Dashboard />} />
        <Route path="/activate" element={<ActivateServer />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
```

- [ ] **Step 6: Update `frontend/src/main.tsx`**

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

- [ ] **Step 7: Commit**

```bash
git add frontend/
git commit -m "feat: scaffold frontend with Firebase config, types, and routing"
```

---

## Task 11: Entry Screen & Server Activation Page (GitHub Issue #11)

**Files:**
- Create: `frontend/src/components/EntryScreen.tsx`
- Create: `frontend/src/components/ActivateServer.tsx`
- Create: `frontend/src/hooks/useAuth.ts`

- [ ] **Step 1: Create `frontend/src/hooks/useAuth.ts`**

```typescript
import { useState, useEffect } from "react";
import { onAuthStateChanged, User, signInWithPopup, signOut } from "firebase/auth";
import { auth, googleProvider } from "../firebase";

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (u) => {
      setUser(u);
      setLoading(false);
    });
    return unsubscribe;
  }, []);

  const signInWithGoogle = () => signInWithPopup(auth, googleProvider);
  const logout = () => signOut(auth);

  return { user, loading, signInWithGoogle, logout };
}
```

- [ ] **Step 2: Create `frontend/src/components/EntryScreen.tsx`**

```tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { doc, getDoc } from "firebase/firestore";
import { db } from "../firebase";

export function EntryScreen() {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleConnect = async () => {
    if (!code.trim()) return;
    setLoading(true);
    setError("");

    const codeDoc = await getDoc(doc(db, "sessionCodes", code.toUpperCase()));
    if (!codeDoc.exists()) {
      setError("Invalid or expired session code.");
      setLoading(false);
      return;
    }

    navigate(`/dashboard/${code.toUpperCase()}`);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "100vh", gap: "24px" }}>
      <h1>Jacky Music</h1>
      <p>Enter your session code to access the playlist</p>

      <div style={{ display: "flex", gap: "8px" }}>
        <input
          type="text"
          value={code}
          onChange={(e) => setCode(e.target.value.toUpperCase())}
          placeholder="Enter session code"
          maxLength={6}
          style={{ fontSize: "1.5rem", textAlign: "center", width: "200px", letterSpacing: "4px" }}
          onKeyDown={(e) => e.key === "Enter" && handleConnect()}
        />
        <button onClick={handleConnect} disabled={loading}>
          {loading ? "Connecting..." : "Connect"}
        </button>
      </div>

      {error && <p style={{ color: "red" }}>{error}</p>}

      <hr style={{ width: "300px", margin: "16px 0" }} />

      <button onClick={() => navigate("/activate")} style={{ opacity: 0.7 }}>
        Server Owner? Activate Your Server
      </button>
    </div>
  );
}
```

- [ ] **Step 3: Create `frontend/src/components/ActivateServer.tsx`**

```tsx
import { useState } from "react";
import { doc, setDoc, serverTimestamp } from "firebase/firestore";
import { db } from "../firebase";
import { useAuth } from "../hooks/useAuth";
import { useNavigate } from "react-router-dom";

export function ActivateServer() {
  const { user, loading: authLoading, signInWithGoogle } = useAuth();
  const [serverId, setServerId] = useState("");
  const [status, setStatus] = useState<"idle" | "saving" | "done" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState("");
  const navigate = useNavigate();

  const handleActivate = async () => {
    if (!serverId.trim() || !user) return;
    setStatus("saving");
    setErrorMsg("");

    try {
      await setDoc(doc(db, "serverOwners", serverId.trim()), {
        ownerDiscordId: "",  // User fills in manually or via bot linking later
        ownerEmail: user.email,
        firebaseUid: user.uid,
        activatedAt: serverTimestamp(),
        isActive: true,
      });
      setStatus("done");
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to activate server.");
      setStatus("error");
    }
  };

  if (authLoading) return <p>Loading...</p>;

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "100vh", gap: "16px" }}>
      <h1>Activate Your Server</h1>

      {!user ? (
        <>
          <p>Sign in with Google to activate Jacky Music for your Discord server.</p>
          <button onClick={signInWithGoogle}>Sign in with Google</button>
        </>
      ) : status === "done" ? (
        <>
          <p>Server <strong>{serverId}</strong> has been activated!</p>
          <p>Jacky Music will now respond to commands in your server.</p>
          <button onClick={() => navigate("/")}>Back to Home</button>
        </>
      ) : (
        <>
          <p>Signed in as <strong>{user.email}</strong></p>
          <p>Enter your Discord Server ID:</p>
          <p style={{ fontSize: "0.85rem", opacity: 0.6 }}>
            (Right-click your server name in Discord → Copy Server ID. Enable Developer Mode in Discord settings if you don't see this option.)
          </p>
          <input
            type="text"
            value={serverId}
            onChange={(e) => setServerId(e.target.value)}
            placeholder="Discord Server ID"
            style={{ fontSize: "1.1rem", width: "280px" }}
          />
          <button onClick={handleActivate} disabled={status === "saving"}>
            {status === "saving" ? "Activating..." : "Activate Server"}
          </button>
          {errorMsg && <p style={{ color: "red" }}>{errorMsg}</p>}
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/EntryScreen.tsx frontend/src/components/ActivateServer.tsx frontend/src/hooks/useAuth.ts
git commit -m "feat: add entry screen with session code input and server activation page"
```

---

## Task 12: Dashboard Layout, Now Playing & Queue (GitHub Issue #12)

**Files:**
- Create: `frontend/src/hooks/useServerState.ts`
- Create: `frontend/src/components/Dashboard.tsx`
- Create: `frontend/src/components/NowPlaying.tsx`
- Create: `frontend/src/components/Queue.tsx`

- [ ] **Step 1: Create `frontend/src/hooks/useServerState.ts`**

```typescript
import { useState, useEffect } from "react";
import { doc, getDoc, onSnapshot } from "firebase/firestore";
import { db } from "../firebase";
import type { ServerState } from "../types";

export function useServerState(sessionCode: string | undefined) {
  const [serverId, setServerId] = useState<string | null>(null);
  const [state, setState] = useState<ServerState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Resolve session code to server ID
  useEffect(() => {
    if (!sessionCode) return;
    getDoc(doc(db, "sessionCodes", sessionCode)).then((snap) => {
      if (snap.exists()) {
        setServerId(snap.data().serverId);
      } else {
        setError("Invalid or expired session code.");
        setLoading(false);
      }
    });
  }, [sessionCode]);

  // Subscribe to server state
  useEffect(() => {
    if (!serverId) return;
    const unsubscribe = onSnapshot(
      doc(db, "servers", serverId),
      (snap) => {
        if (snap.exists()) {
          setState(snap.data() as ServerState);
        } else {
          setError("Server not found.");
        }
        setLoading(false);
      },
      (err) => {
        setError(err.message);
        setLoading(false);
      }
    );
    return unsubscribe;
  }, [serverId]);

  return { serverId, state, error, loading };
}
```

- [ ] **Step 2: Create `frontend/src/components/NowPlaying.tsx`**

```tsx
import { useState, useEffect } from "react";
import type { CurrentTrack } from "../types";

interface Props {
  track: CurrentTrack | null;
  isPaused: boolean;
}

export function NowPlaying({ track, isPaused }: Props) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!track || isPaused) return;
    const started = new Date(track.startedAt).getTime();
    const interval = setInterval(() => {
      setElapsed(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, [track, isPaused]);

  if (!track) {
    return (
      <div style={{ padding: "24px", textAlign: "center" }}>
        <p>Nothing is playing</p>
      </div>
    );
  }

  const progress = Math.min(elapsed / track.duration, 1);
  const formatTime = (s: number) => {
    const m = Math.floor(s / 60);
    return `${m}:${String(s % 60).padStart(2, "0")}`;
  };

  return (
    <div style={{ padding: "16px", display: "flex", gap: "16px", alignItems: "center" }}>
      {track.thumbnail && (
        <img src={track.thumbnail} alt="" style={{ width: "80px", height: "80px", borderRadius: "8px" }} />
      )}
      <div style={{ flex: 1 }}>
        <h3 style={{ margin: 0 }}>{track.title}</h3>
        <p style={{ margin: "4px 0", opacity: 0.7 }}>{track.artist}</p>
        <div style={{ background: "#333", borderRadius: "4px", height: "6px", marginTop: "8px" }}>
          <div style={{ background: "#1DB954", borderRadius: "4px", height: "100%", width: `${progress * 100}%`, transition: "width 1s linear" }} />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8rem", marginTop: "4px" }}>
          <span>{formatTime(Math.min(elapsed, track.duration))}</span>
          <span>{formatTime(track.duration)}</span>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Create `frontend/src/components/Queue.tsx`**

```tsx
import { doc, updateDoc } from "firebase/firestore";
import { db } from "../firebase";
import type { Track } from "../types";

interface Props {
  queue: Track[];
  serverId: string;
}

export function Queue({ queue, serverId }: Props) {
  const removeTrack = async (index: number) => {
    const updated = [...queue];
    updated.splice(index, 1);
    await updateDoc(doc(db, "servers", serverId), { queue: updated });
  };

  const moveTrack = async (from: number, to: number) => {
    const updated = [...queue];
    const [track] = updated.splice(from, 1);
    updated.splice(to, 0, track);
    await updateDoc(doc(db, "servers", serverId), { queue: updated });
  };

  if (queue.length === 0) {
    return <p style={{ textAlign: "center", opacity: 0.5 }}>Queue is empty</p>;
  }

  return (
    <div>
      <h3>Queue ({queue.length} tracks)</h3>
      <ul style={{ listStyle: "none", padding: 0 }}>
        {queue.map((track, i) => {
          const mins = Math.floor(track.duration / 60);
          const secs = track.duration % 60;
          return (
            <li key={`${track.url}-${i}`} style={{ display: "flex", alignItems: "center", padding: "8px", gap: "8px", borderBottom: "1px solid #333" }}>
              <span style={{ width: "30px", textAlign: "center", opacity: 0.5 }}>{i + 1}</span>
              {track.thumbnail && (
                <img src={track.thumbnail} alt="" style={{ width: "40px", height: "40px", borderRadius: "4px" }} />
              )}
              <div style={{ flex: 1 }}>
                <strong>{track.title}</strong>
                <br />
                <span style={{ fontSize: "0.85rem", opacity: 0.7 }}>{track.artist} — {mins}:{String(secs).padStart(2, "0")}</span>
              </div>
              <button onClick={() => i > 0 && moveTrack(i, i - 1)} disabled={i === 0} title="Move up">
                &uarr;
              </button>
              <button onClick={() => i < queue.length - 1 && moveTrack(i, i + 1)} disabled={i === queue.length - 1} title="Move down">
                &darr;
              </button>
              <button onClick={() => removeTrack(i)} title="Remove">
                &times;
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
```

- [ ] **Step 4: Create `frontend/src/components/Dashboard.tsx`**

```tsx
import { useParams, useNavigate } from "react-router-dom";
import { useServerState } from "../hooks/useServerState";
import { NowPlaying } from "./NowPlaying";
import { Queue } from "./Queue";
import { PlaybackControls } from "./PlaybackControls";
import { SearchPanel } from "./SearchPanel";
import { PlaylistManager } from "./PlaylistManager";
import { HistoryPanel } from "./HistoryPanel";

export function Dashboard() {
  const { sessionCode } = useParams<{ sessionCode: string }>();
  const { serverId, state, error, loading } = useServerState(sessionCode);
  const navigate = useNavigate();

  if (loading) return <p>Loading...</p>;
  if (error || !state || !serverId) {
    return (
      <div style={{ textAlign: "center", padding: "48px" }}>
        <p style={{ color: "red" }}>{error || "Session not found."}</p>
        <button onClick={() => navigate("/")}>Back</button>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: "900px", margin: "0 auto", padding: "16px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>Jacky Music</h1>
        <span style={{ opacity: 0.5 }}>Session: {sessionCode}</span>
      </div>

      <NowPlaying track={state.currentTrack} isPaused={state.isPaused} />
      <PlaybackControls state={state} serverId={serverId} />
      <SearchPanel serverId={serverId} />
      <Queue queue={state.queue} serverId={serverId} />
      <PlaylistManager serverId={serverId} currentQueue={state.queue} currentTrack={state.currentTrack} />
      <HistoryPanel serverId={serverId} />
    </div>
  );
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useServerState.ts frontend/src/components/Dashboard.tsx frontend/src/components/NowPlaying.tsx frontend/src/components/Queue.tsx
git commit -m "feat: add dashboard with now playing display and queue management"
```

---

## Task 13: Playback Controls Component (GitHub Issue #13)

**Files:**
- Create: `frontend/src/components/PlaybackControls.tsx`

- [ ] **Step 1: Write `frontend/src/components/PlaybackControls.tsx`**

```tsx
import { doc, updateDoc, arrayUnion } from "firebase/firestore";
import { db } from "../firebase";
import type { ServerState } from "../types";

interface Props {
  state: ServerState;
  serverId: string;
}

export function PlaybackControls({ state, serverId }: Props) {
  const ref = doc(db, "servers", serverId);

  const togglePause = () => updateDoc(ref, { isPaused: !state.isPaused });

  const skip = () => updateDoc(ref, { currentTrack: null, isPlaying: true });

  const shuffle = () => {
    const shuffled = [...state.queue].sort(() => Math.random() - 0.5);
    updateDoc(ref, { queue: shuffled });
  };

  const cycleLoop = () => {
    const cycle: Record<string, string> = { off: "track", track: "queue", queue: "off" };
    updateDoc(ref, { loopMode: cycle[state.loopMode] });
  };

  const setVolume = (vol: number) => updateDoc(ref, { volume: vol });

  const loopLabel: Record<string, string> = { off: "Loop: Off", track: "Loop: Track", queue: "Loop: Queue" };

  return (
    <div style={{ display: "flex", gap: "12px", alignItems: "center", padding: "12px 0", flexWrap: "wrap" }}>
      <button onClick={togglePause}>
        {state.isPaused ? "Resume" : "Pause"}
      </button>
      <button onClick={skip} disabled={!state.isPlaying}>Skip</button>
      <button onClick={shuffle} disabled={state.queue.length < 2}>Shuffle</button>
      <button onClick={cycleLoop}>{loopLabel[state.loopMode]}</button>

      <div style={{ display: "flex", alignItems: "center", gap: "8px", marginLeft: "auto" }}>
        <span style={{ fontSize: "0.85rem" }}>Vol: {state.volume}%</span>
        <input
          type="range"
          min={0}
          max={100}
          value={state.volume}
          onChange={(e) => setVolume(Number(e.target.value))}
          style={{ width: "120px" }}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/PlaybackControls.tsx
git commit -m "feat: add playback controls — pause, skip, shuffle, loop, volume"
```

---

## Task 14: YouTube Search Cloud Function & Search Panel (GitHub Issue #14)

**Files:**
- Create: `functions/package.json`
- Create: `functions/tsconfig.json`
- Create: `functions/src/index.ts`
- Create: `frontend/src/services/api.ts`
- Create: `frontend/src/components/SearchPanel.tsx`

- [ ] **Step 1: Create `functions/package.json`**

```json
{
  "name": "jacky-music-functions",
  "scripts": {
    "build": "tsc",
    "serve": "npm run build && firebase emulators:start --only functions",
    "deploy": "firebase deploy --only functions"
  },
  "main": "lib/index.js",
  "dependencies": {
    "firebase-admin": "^12.0.0",
    "firebase-functions": "^5.0.0"
  },
  "devDependencies": {
    "typescript": "^5.4.0"
  }
}
```

- [ ] **Step 2: Create `functions/tsconfig.json`**

```json
{
  "compilerOptions": {
    "module": "commonjs",
    "noImplicitReturns": true,
    "noUnusedLocals": true,
    "outDir": "lib",
    "sourceMap": true,
    "strict": true,
    "target": "es2020"
  },
  "compileOnSave": true,
  "include": ["src"]
}
```

- [ ] **Step 3: Create `functions/src/index.ts`**

```typescript
import { onRequest } from "firebase-functions/v2/https";
import { defineString } from "firebase-functions/params";

const youtubeApiKey = defineString("YOUTUBE_API_KEY");

export const searchYouTube = onRequest({ cors: true }, async (req, res) => {
  const query = req.query.q as string;
  if (!query) {
    res.status(400).json({ error: "Missing query parameter 'q'" });
    return;
  }

  const maxResults = Math.min(Number(req.query.maxResults) || 10, 20);
  const url = `https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&videoCategoryId=10&maxResults=${maxResults}&q=${encodeURIComponent(query)}&key=${youtubeApiKey.value()}`;

  const response = await fetch(url);
  const data = await response.json();

  if (!response.ok) {
    res.status(response.status).json({ error: data.error?.message || "YouTube API error" });
    return;
  }

  const results = (data.items || []).map((item: any) => ({
    videoId: item.id.videoId,
    title: item.snippet.title,
    artist: item.snippet.channelTitle,
    thumbnail: item.snippet.thumbnails?.medium?.url || "",
    url: `https://www.youtube.com/watch?v=${item.id.videoId}`,
  }));

  res.json({ results });
});
```

- [ ] **Step 4: Create `frontend/src/services/api.ts`**

```typescript
const FUNCTIONS_BASE = import.meta.env.VITE_FUNCTIONS_URL || "";

export interface SearchResult {
  videoId: string;
  title: string;
  artist: string;
  thumbnail: string;
  url: string;
}

export async function searchYouTube(query: string): Promise<SearchResult[]> {
  const res = await fetch(
    `${FUNCTIONS_BASE}/searchYouTube?q=${encodeURIComponent(query)}&maxResults=10`
  );
  if (!res.ok) throw new Error("Search failed");
  const data = await res.json();
  return data.results;
}
```

- [ ] **Step 5: Create `frontend/src/components/SearchPanel.tsx`**

```tsx
import { useState } from "react";
import { doc, updateDoc, arrayUnion } from "firebase/firestore";
import { db } from "../firebase";
import { searchYouTube, type SearchResult } from "../services/api";

interface Props {
  serverId: string;
}

export function SearchPanel({ serverId }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSearch = async () => {
    if (!query.trim()) return;
    setLoading(true);
    setError("");
    try {
      const data = await searchYouTube(query);
      setResults(data);
    } catch {
      setError("Search failed. Try again.");
    }
    setLoading(false);
  };

  const addToQueue = async (result: SearchResult) => {
    await updateDoc(doc(db, "servers", serverId), {
      queue: arrayUnion({
        title: result.title,
        artist: result.artist,
        url: result.url,
        thumbnail: result.thumbnail,
        duration: 0,  // Duration resolved by bot when playing
        requestedBy: "Web User",
      }),
    });
    setResults((prev) => prev.filter((r) => r.videoId !== result.videoId));
  };

  return (
    <div style={{ padding: "16px 0" }}>
      <h3>Search</h3>
      <div style={{ display: "flex", gap: "8px" }}>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search YouTube for music..."
          style={{ flex: 1 }}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
        />
        <button onClick={handleSearch} disabled={loading}>
          {loading ? "Searching..." : "Search"}
        </button>
      </div>

      {error && <p style={{ color: "red" }}>{error}</p>}

      {results.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0, marginTop: "12px" }}>
          {results.map((r) => (
            <li key={r.videoId} style={{ display: "flex", alignItems: "center", gap: "8px", padding: "8px", borderBottom: "1px solid #333" }}>
              {r.thumbnail && (
                <img src={r.thumbnail} alt="" style={{ width: "60px", height: "45px", borderRadius: "4px" }} />
              )}
              <div style={{ flex: 1 }}>
                <strong>{r.title}</strong>
                <br />
                <span style={{ fontSize: "0.85rem", opacity: 0.7 }}>{r.artist}</span>
              </div>
              <button onClick={() => addToQueue(r)}>+ Add</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 6: Commit**

```bash
git add functions/ frontend/src/services/api.ts frontend/src/components/SearchPanel.tsx
git commit -m "feat: add YouTube search Cloud Function and search panel component"
```

---

## Task 15: Playlist Manager & History Panel (GitHub Issue #15)

**Files:**
- Create: `frontend/src/components/PlaylistManager.tsx`
- Create: `frontend/src/components/HistoryPanel.tsx`

- [ ] **Step 1: Create `frontend/src/components/PlaylistManager.tsx`**

```tsx
import { useState, useEffect } from "react";
import { collection, getDocs, doc, setDoc, deleteDoc, updateDoc, arrayUnion, serverTimestamp } from "firebase/firestore";
import { db } from "../firebase";
import type { Track, CurrentTrack, Playlist } from "../types";

interface Props {
  serverId: string;
  currentQueue: Track[];
  currentTrack: CurrentTrack | null;
}

export function PlaylistManager({ serverId, currentQueue, currentTrack }: Props) {
  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [saveName, setSaveName] = useState("");
  const [expanded, setExpanded] = useState(false);

  const fetchPlaylists = async () => {
    const snap = await getDocs(collection(db, "servers", serverId, "playlists"));
    setPlaylists(snap.docs.map((d) => ({ name: d.id, ...d.data() } as Playlist)));
  };

  useEffect(() => {
    if (expanded) fetchPlaylists();
  }, [expanded, serverId]);

  const savePlaylist = async () => {
    if (!saveName.trim()) return;
    const tracks: Track[] = [];
    if (currentTrack) {
      tracks.push({
        title: currentTrack.title,
        artist: currentTrack.artist,
        url: currentTrack.url,
        thumbnail: currentTrack.thumbnail,
        duration: currentTrack.duration,
        requestedBy: currentTrack.requestedBy,
      });
    }
    tracks.push(...currentQueue);
    if (tracks.length === 0) return;

    await setDoc(doc(db, "servers", serverId, "playlists", saveName.trim()), {
      name: saveName.trim(),
      tracks,
      createdBy: "Web User",
      createdAt: serverTimestamp(),
    });
    setSaveName("");
    fetchPlaylists();
  };

  const loadPlaylist = async (playlist: Playlist) => {
    const tracksToAdd = playlist.tracks.map((t) => ({
      ...t,
      requestedBy: "Web User",
    }));
    for (const t of tracksToAdd) {
      await updateDoc(doc(db, "servers", serverId), {
        queue: arrayUnion(t),
      });
    }
  };

  const deletePlaylist = async (name: string) => {
    await deleteDoc(doc(db, "servers", serverId, "playlists", name));
    fetchPlaylists();
  };

  return (
    <div style={{ padding: "16px 0" }}>
      <h3 onClick={() => setExpanded(!expanded)} style={{ cursor: "pointer" }}>
        Saved Playlists {expanded ? "▾" : "▸"}
      </h3>

      {expanded && (
        <>
          <div style={{ display: "flex", gap: "8px", marginBottom: "12px" }}>
            <input
              type="text"
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
              placeholder="Playlist name"
              style={{ flex: 1 }}
            />
            <button onClick={savePlaylist} disabled={!saveName.trim()}>Save Current</button>
          </div>

          {playlists.length === 0 ? (
            <p style={{ opacity: 0.5 }}>No saved playlists yet.</p>
          ) : (
            <ul style={{ listStyle: "none", padding: 0 }}>
              {playlists.map((p) => (
                <li key={p.name} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px", borderBottom: "1px solid #333" }}>
                  <div>
                    <strong>{p.name}</strong>
                    <span style={{ marginLeft: "8px", opacity: 0.5 }}>{p.tracks.length} tracks</span>
                  </div>
                  <div style={{ display: "flex", gap: "8px" }}>
                    <button onClick={() => loadPlaylist(p)}>Load</button>
                    <button onClick={() => deletePlaylist(p.name)}>Delete</button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create `frontend/src/components/HistoryPanel.tsx`**

```tsx
import { useState, useEffect } from "react";
import { collection, getDocs, query, orderBy, limit, doc, updateDoc, arrayUnion } from "firebase/firestore";
import { db } from "../firebase";
import type { HistorySession } from "../types";

interface Props {
  serverId: string;
}

export function HistoryPanel({ serverId }: Props) {
  const [sessions, setSessions] = useState<HistorySession[]>([]);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!expanded) return;
    const q = query(
      collection(db, "servers", serverId, "history"),
      orderBy("startedAt", "desc"),
      limit(5)
    );
    getDocs(q).then((snap) => {
      setSessions(snap.docs.map((d) => ({ id: d.id, ...d.data() } as HistorySession)));
    });
  }, [expanded, serverId]);

  const requeueSession = async (session: HistorySession) => {
    for (const track of session.tracks) {
      await updateDoc(doc(db, "servers", serverId), {
        queue: arrayUnion({
          title: track.title,
          artist: track.artist,
          url: track.url,
          thumbnail: track.thumbnail,
          duration: track.duration,
          requestedBy: "Web User",
        }),
      });
    }
  };

  return (
    <div style={{ padding: "16px 0" }}>
      <h3 onClick={() => setExpanded(!expanded)} style={{ cursor: "pointer" }}>
        History {expanded ? "▾" : "▸"}
      </h3>

      {expanded && (
        sessions.length === 0 ? (
          <p style={{ opacity: 0.5 }}>No history yet.</p>
        ) : (
          <div>
            {sessions.map((s) => (
              <div key={s.id} style={{ marginBottom: "16px", padding: "12px", border: "1px solid #333", borderRadius: "8px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <strong>{s.startedAt?.slice(0, 10) || "Unknown date"}</strong>
                  <button onClick={() => requeueSession(s)}>Re-queue All</button>
                </div>
                <ul style={{ paddingLeft: "20px", marginTop: "8px" }}>
                  {s.tracks.slice(0, 5).map((t, i) => (
                    <li key={i} style={{ opacity: 0.7 }}>{t.title} — {t.artist}</li>
                  ))}
                  {s.tracks.length > 5 && (
                    <li style={{ opacity: 0.5 }}>...and {s.tracks.length - 5} more</li>
                  )}
                </ul>
              </div>
            ))}
          </div>
        )
      )}
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/PlaylistManager.tsx frontend/src/components/HistoryPanel.tsx
git commit -m "feat: add playlist manager and history panel components"
```

---

## Task 16: Docker Compose & Lavalink Configuration (GitHub Issue #16)

**Files:**
- Create: `docker-compose.yml`
- Create: `lavalink/application.yml`

- [ ] **Step 1: Create `lavalink/application.yml`**

```yaml
server:
  port: 2333
  address: 0.0.0.0

lavalink:
  server:
    password: "youshallnotpass"
    sources:
      youtube: true
      bandcamp: true
      soundcloud: true
      twitch: true
      vimeo: true
      http: true
      local: false
    bufferDurationMs: 400
    frameBufferDurationMs: 5000
    youtubePlaylistLoadLimit: 50
    playerUpdateInterval: 5
    youtubeSearchEnabled: true
    gc-warnings: true

logging:
  level:
    root: INFO
    lavalink: INFO
```

- [ ] **Step 2: Create `docker-compose.yml`**

```yaml
services:
  lavalink:
    image: ghcr.io/lavalink-devs/lavalink:4
    container_name: jacky-lavalink
    restart: unless-stopped
    ports:
      - "2333:2333"
    volumes:
      - ./lavalink/application.yml:/opt/Lavalink/application.yml
    environment:
      - _JAVA_OPTIONS=-Xmx512m

  jacky-bot:
    build: ./bot
    container_name: jacky-bot
    restart: unless-stopped
    depends_on:
      - lavalink
    env_file:
      - .env
    environment:
      - LAVALINK_HOST=lavalink
      - LAVALINK_PORT=2333
      - LAVALINK_PASSWORD=youshallnotpass
    volumes:
      - ./bot:/app
```

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml lavalink/
git commit -m "feat: add Docker Compose config with Lavalink and bot services"
```

---

## Task 17: Firebase Hosting & Deployment Config (GitHub Issue #17)

**Files:**
- Create: `frontend/firebase.json`
- Create: `frontend/.firebaserc`
- Create: `firebase.json` (root — delegates to frontend and functions)

- [ ] **Step 1: Create root `firebase.json`**

```json
{
  "hosting": {
    "public": "frontend/dist",
    "ignore": ["firebase.json", "**/.*", "**/node_modules/**"],
    "rewrites": [
      { "source": "**", "destination": "/index.html" }
    ]
  },
  "functions": {
    "source": "functions",
    "runtime": "nodejs20"
  }
}
```

- [ ] **Step 2: Create `.firebaserc`**

```json
{
  "projects": {
    "default": "your-firebase-project-id"
  }
}
```

- [ ] **Step 3: Update `README.md` with full setup and deployment instructions**

```markdown
# Jacky Music

Discord music bot with a companion web app for playlist management.

## Architecture

- **Bot**: Python (discord.py + wavelink) — runs on GCP VM via Docker
- **Audio**: Lavalink v4 — self-hosted audio streaming server
- **Web App**: React + Vite + TypeScript — hosted on Firebase
- **Data**: Firebase Firestore — real-time playlist sync
- **Auth**: Firebase Auth (Google) — server owner activation

## Prerequisites

- Python 3.11+
- Node.js 20+
- Docker & Docker Compose
- Firebase CLI (`npm install -g firebase-tools`)
- Discord Bot Token ([Discord Developer Portal](https://discord.com/developers/applications))
- Spotify App Credentials ([Spotify Developer Dashboard](https://developer.spotify.com/dashboard))
- YouTube Data API Key ([Google Cloud Console](https://console.cloud.google.com))
- Firebase Project with Firestore, Auth (Google provider), and Hosting enabled

## Setup

### 1. Clone and configure
\`\`\`bash
git clone https://github.com/chlgustjr41/discord-music-bot.git
cd discord-music-bot
cp .env.example .env
# Fill in all values in .env
\`\`\`

### 2. Firebase setup
\`\`\`bash
firebase login
firebase use --add  # Select your Firebase project
cd functions && npm install && cd ..
cd frontend && npm install && cd ..
\`\`\`

### 3. Local development
\`\`\`bash
# Terminal 1: Bot + Lavalink
docker compose up -d lavalink
cd bot && pip install -r requirements.txt && python main.py

# Terminal 2: Web app
cd frontend && npm run dev
\`\`\`

### 4. Deploy to GCP VM
\`\`\`bash
# On the VM:
git clone https://github.com/chlgustjr41/discord-music-bot.git
cd discord-music-bot
cp .env.example .env  # Fill in values
docker compose up -d --build
\`\`\`

### 5. Deploy web app
\`\`\`bash
cd frontend && npm run build
firebase deploy --only hosting
firebase deploy --only functions
\`\`\`

## GCP VM Spec

| Spec | Value |
|---|---|
| Machine type | e2-small (2 vCPU, 2GB RAM) |
| OS | Ubuntu 22.04 LTS |
| Disk | 20GB standard persistent |
| Estimated cost | ~$13/mo |

## Bot Commands

| Command | Description |
|---|---|
| `j!play <query/URL>` | Play a song or add to queue |
| `j!pause` | Pause playback |
| `j!resume` | Resume playback |
| `j!skip` | Skip current track |
| `j!stop` | Stop and disconnect |
| `j!queue` | Show queue |
| `j!nowplaying` | Show current track |
| `j!remove <pos>` | Remove track from queue |
| `j!move <from> <to>` | Reorder queue |
| `j!shuffle` | Shuffle queue |
| `j!loop` | Toggle loop mode |
| `j!volume <0-100>` | Set volume |
| `j!playlist save/load/list/delete` | Manage playlists |
| `j!history` | Show play history |
| `j!session` | Show session code |
```

- [ ] **Step 4: Commit**

```bash
git add firebase.json .firebaserc README.md
git commit -m "feat: add Firebase hosting config and comprehensive README"
```

---

## Task Summary

| Task | Description | GitHub Issue |
|---|---|---|
| 1 | Project scaffold & configuration | #1 |
| 2 | Firestore client service | #2 |
| 3 | Session manager & embed utilities | #3 |
| 4 | Server activation check cog | #4 |
| 5 | Playback cog (play, pause, resume, skip, stop, volume, loop, idle) | #5 |
| 6 | Queue management cog | #6 |
| 7 | Playlist, history & session cogs | #7 |
| 8 | Spotify link resolution | #8 |
| 9 | Firestore listener (web → bot sync) | #9 |
| 10 | Firebase & frontend scaffold | #10 |
| 11 | Entry screen & server activation page | #11 |
| 12 | Dashboard, now playing & queue components | #12 |
| 13 | Playback controls component | #13 |
| 14 | YouTube search Cloud Function & search panel | #14 |
| 15 | Playlist manager & history panel | #15 |
| 16 | Docker Compose & Lavalink configuration | #16 |
| 17 | Firebase hosting & deployment config | #17 |
