# Jacky Music

Discord music bot with a companion web dashboard for real-time playlist management. Users control playback from Discord or the web app — both stay in sync via Firestore.

## Architecture (v2 — stability rewrite in progress)

Four crash-only Docker services on one VM; state lives in Firestore + a
token volume, so any container can be restarted at any time.

| Service | Role |
|---------|------|
| `services/bot` | Discord commands, voice, playback (stateless) |
| `services/lavalink` | Audio engine (templated config, layered YouTube auth) |
| `services/token-minter` | Scheduled poToken mint (M2) |
| `services/guardian` | Canary probe → classify (F1–F9) → restart/alert |

Docs: [Architecture](docs/architecture/ARCHITECTURE.md) ·
[Runbook](docs/operations/RUNBOOK.md) ·
[Deployment](docs/operations/DEPLOYMENT.md) ·
[Decisions](docs/architecture/decisions/) ·
[Roadmap](docs/roadmap/FUTURE.md)

Quickstart: `cp deploy/.env.example deploy/.env`, fill it, `make up`.
All commands: `make help`.

> Legacy `bot/` + root `docker-compose.yml` remain in production until the
> M5 cutover; do not add features there.

## Prerequisites

- Python 3.11+
- Node.js 20+
- Docker & Docker Compose
- Firebase CLI (`npm install -g firebase-tools`)
- Discord Bot Token ([Developer Portal](https://discord.com/developers/applications))
- YouTube Data API Key ([Google Cloud Console](https://console.cloud.google.com))
- Firebase Project with Firestore, Auth (Google provider), and Hosting enabled

## Setup

### 1. Clone and configure

```bash
git clone https://github.com/chlgustjr41/discord-music-bot.git
cd discord-music-bot
cp .env.example .env
# Fill in all values in .env
```

### 2. Firebase setup

```bash
firebase login
firebase use --add  # Select your Firebase project
cd functions && npm install && cd ..
cd frontend && npm install && cd ..
```

The frontend needs its own environment variables. Create `frontend/.env`:

```
VITE_FIREBASE_API_KEY=...
VITE_FIREBASE_AUTH_DOMAIN=...
VITE_FIREBASE_PROJECT_ID=...
VITE_FIREBASE_STORAGE_BUCKET=...
VITE_FIREBASE_MESSAGING_SENDER_ID=...
VITE_FIREBASE_APP_ID=...
```

### 3. Local development

```bash
# Terminal 1: Lavalink
docker compose up -d lavalink

# Terminal 2: Bot
cd bot && pip install -r requirements.txt && python main.py

# Terminal 3: Web app
cd frontend && npm run dev
```

### 4. Deploy

See [PRODUCTION.md](PRODUCTION.md) for full deployment instructions.

## Bot Commands

All commands use the `j!` prefix.

### Playback

| Command | Description |
|---------|-------------|
| `j!play <query/URL>` | Play a song or add to queue (YouTube, SoundCloud, Bandcamp) |
| `j!pause` | Pause playback |
| `j!resume` | Resume playback |
| `j!skip` | Skip current track |
| `j!stop` | Stop playback and disconnect |
| `j!nowplaying` | Show current track info |
| `j!volume <0-100>` | Set volume |
| `j!loop` | Cycle loop mode: off → track → queue |

### Queue

| Command | Description |
|---------|-------------|
| `j!queue [page]` | Show queue (paginated, 10 per page) |
| `j!remove <position>` | Remove track by position |
| `j!move <from> <to>` | Reorder a track |
| `j!shuffle` | Shuffle the queue |

### Playlists & History

| Command | Description |
|---------|-------------|
| `j!playlist save <name>` | Save current queue as a playlist |
| `j!playlist load <name>` | Load a saved playlist into queue |
| `j!playlist list` | List all saved playlists |
| `j!playlist delete <name>` | Delete a playlist |
| `j!history` | Show recent play sessions |
| `j!session` | Show session code and web link |

### Local Audio Node

Run a local Lavalink instance for lower latency audio. See [jacky-music-local](https://github.com/chlgustjr41/jacky-music-local) for the setup.

| Command | Description |
|---------|-------------|
| `j!localnode connect <url> <password>` | Connect to your local Lavalink node |
| `j!localnode disconnect` | Switch back to cloud audio |
| `j!localnode status` | Show which audio backend is active |

## Web Dashboard

When the bot joins a voice channel, it generates a 6-character session code. Anyone with the code can access the web dashboard to:

- View now-playing with live progress and seek
- Control playback (play/pause, skip, shuffle, loop, volume)
- Search YouTube and add tracks to queue
- Drag-and-drop reorder the queue
- Create, save, and load playlists (from queue, history, or YouTube playlist URL)
- View command and music history
- See real-time toast notifications for all session activity

## Project Structure

```
discord-music-bot/
├── bot/                    # Python Discord bot
│   ├── main.py             # Entry point, Lavalink + Firebase init
│   ├── config.py           # Environment variables and constants
│   ├── player.py           # Custom wavelink Player (Lavalink 4.2+ compat)
│   ├── cogs/
│   │   ├── activation.py   # Server activation gate
│   │   ├── playback.py     # Core playback engine + auto-play logic
│   │   ├── queue_cmd.py    # Queue management commands
│   │   ├── playlist_cmd.py # Playlist save/load/list/delete
│   │   ├── history_cmd.py  # Play history command
│   │   ├── session_cmd.py  # Session code display
│   │   └── localnode_cmd.py # Local Lavalink node management
│   ├── services/
│   │   ├── firestore_client.py   # Full Firestore ORM (state, queue, playlists, history)
│   │   ├── firestore_listener.py # Real-time listener for web app changes
│   │   ├── session_manager.py    # Session code generation
│   │   └── spotify_client.py     # Spotify URL detection (stub)
│   └── utils/
│       └── embeds.py       # Discord embed builders
├── frontend/               # React web app
│   └── src/
│       ├── App.tsx          # Router + toast provider
│       ├── firebase.ts      # Firebase SDK init
│       ├── types.ts         # Shared TypeScript interfaces
│       ├── components/
│       │   ├── EntryScreen.tsx      # Landing (session code input)
│       │   ├── ActivateServer.tsx   # Google login + server activation
│       │   ├── Dashboard.tsx        # Main dashboard layout
│       │   ├── NowPlaying.tsx       # Current track + seek slider
│       │   ├── PlaybackControls.tsx # Play/pause/skip/shuffle/loop/volume
│       │   ├── Queue.tsx            # Drag-to-reorder queue
│       │   ├── SearchPanel.tsx      # YouTube search + add to queue
│       │   ├── PlaylistManager.tsx  # Playlist CRUD + YouTube import
│       │   ├── CommandHistory.tsx   # Command usage history
│       │   └── MusicHistory.tsx     # Track play history
│       ├── hooks/
│       │   ├── useServerState.ts    # Real-time Firestore subscription
│       │   ├── useAuth.ts           # Firebase Auth (Google login)
│       │   └── useActivityToasts.tsx # Toast notifications for state changes
│       └── services/
│           └── api.ts       # Cloud Function proxy for YouTube search
├── functions/              # Firebase Cloud Functions
│   └── src/index.ts        # YouTube Data API v3 search proxy
├── lavalink/
│   └── application.yml     # Lavalink server config
├── deploy/
│   └── startup.sh          # GCP VM Docker installation script
├── docker-compose.yml      # Bot + Lavalink services
├── firebase.json           # Firebase project config
├── firestore.rules         # Firestore security rules
└── .env.example            # Environment variable template
```

## Firestore Data Model

```
discord-music-bot (database)
├── sessionCodes/{code}              # Session code → server mapping
│   ├── serverId
│   └── createdAt
├── servers/{serverId}               # Server playback state
│   ├── sessionCode, currentTrack, queue[], isPlaying, isPaused
│   ├── loopMode, volume, voiceChannelId, voiceChannelName
│   ├── seekPosition, searchQuery, searchResults[]
│   ├── serverName, serverIcon
│   └── subcollections:
│       ├── playlists/{name}         # Saved playlists
│       ├── musicHistory/{docId}     # Per-track play counts
│       └── commandHistory/{docId}   # Per-command usage counts
└── serverOwners/{serverId}          # Activation records
    ├── ownerEmail, firebaseUid
    ├── activatedAt, isActive
```

## License

Private project.
