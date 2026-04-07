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

### 3. Local development
```bash
# Terminal 1: Bot + Lavalink
docker compose up -d lavalink
cd bot && pip install -r requirements.txt && python main.py

# Terminal 2: Web app
cd frontend && npm run dev
```

### 4. Deploy to GCP VM
```bash
# On the VM:
git clone https://github.com/chlgustjr41/discord-music-bot.git
cd discord-music-bot
cp .env.example .env  # Fill in values
docker compose up -d --build
```

### 5. Deploy web app
```bash
cd frontend && npm run build
firebase deploy --only hosting
firebase deploy --only functions
```

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
