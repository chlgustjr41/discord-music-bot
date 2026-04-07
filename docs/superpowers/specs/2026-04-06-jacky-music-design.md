# Jacky Music — Discord Music Bot Design Spec

**Date:** 2026-04-06
**Status:** Approved

## Overview

Jacky Music is a Discord music bot that streams audio in voice channels and provides a companion web app for playlist management. Users interact via `j!` prefix commands in Discord or through a web dashboard accessed with a rotating session code.

Target scale: small community (<10 Discord servers), self-hosted on a single GCP VM.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  GCP e2-small VM (Docker Compose)               │
│  ┌─────────────────┐  ┌──────────────────────┐  │
│  │  Python Bot      │──│  Lavalink Server v4  │  │
│  │  (discord.py +   │  │  (Java, audio        │  │
│  │   wavelink)      │  │   streaming)         │  │
│  └────────┬─────────┘  └──────────────────────┘  │
│           │                                      │
└───────────┼──────────────────────────────────────┘
            │
            ▼
┌──────────────────────────┐
│  Firebase (Google Cloud)  │
│  ├─ Firestore             │  ← playlist state, session codes,
│  │                        │    saved playlists, play history
│  ├─ Auth (Google login)    │  ← server owner authentication
│  ├─ Hosting               │  ← React+Vite web app
│  └─ Cloud Functions       │  ← YouTube search proxy
└──────────────────────────┘
            ▲
            │
┌───────────┴──────────────┐
│  Web App (React+Vite+TS) │
│  Firestore real-time      │
│  listeners for live sync  │
└──────────────────────────┘
```

**Data flow:**
1. User issues a `j!` command in Discord or interacts with the web app.
2. Both interfaces read/write to the same Firestore documents.
3. The bot listens to Firestore changes — when a song is added from the web, the bot picks it up and queues it in Lavalink.
4. Lavalink handles audio extraction (YouTube via Lavaplayer) and streams to Discord voice.

Firestore is the single source of truth for all playlist state.

## Discord Bot Commands

Bot name: **Jacky Music**
Prefix: `j!`

| Command | Description |
|---|---|
| `j!play <query or URL>` | Join voice channel (if not already in one), search YouTube/resolve Spotify link, add to queue, start playing if idle |
| `j!pause` | Pause playback |
| `j!resume` | Resume playback |
| `j!skip` | Skip to next track |
| `j!stop` | Stop playback, clear queue, leave voice channel |
| `j!queue` | Show current queue (paginated) |
| `j!nowplaying` | Show current track with progress |
| `j!remove <position>` | Remove a track from queue by position |
| `j!move <from> <to>` | Reorder a track in the queue |
| `j!shuffle` | Shuffle the current queue |
| `j!loop` | Toggle loop mode: off / single track / queue |
| `j!volume <0-100>` | Set playback volume |
| `j!playlist save <name>` | Save current queue as a named playlist |
| `j!playlist load <name>` | Load a saved playlist into the queue |
| `j!playlist list` | List saved playlists for this server |
| `j!playlist delete <name>` | Delete a saved playlist |
| `j!history` | Show recently played tracks from past sessions |
| `j!session` | Show the current session code for web access |

**Behavior:**
- When the bot is first added to a server, the server owner must complete Google login via Firebase Auth through the web app to activate the bot. The bot responds to commands only in activated servers.
- Bot auto-generates a new 6-character alphanumeric session code and posts it in the text channel when it joins voice.
- Bot leaves voice after 5 minutes idle (empty queue and no listeners).
- `j!play` with a Spotify link resolves the track name via Spotify API, then searches YouTube through Lavalink.
- Playlist state persists per server even when the bot is not in a voice channel.

## Firestore Data Model

### `serverOwners/{serverId}`
```json
{
  "ownerDiscordId": "123456789",
  "ownerEmail": "owner@gmail.com",
  "firebaseUid": "...",
  "activatedAt": "Timestamp",
  "isActive": true
}
```

The bot checks this collection on every command. If the server has no active owner record, the bot replies with a link to the web app for the server owner to authenticate via Google login.

**Future: whitelisting.** A `whitelist` collection can be added later to restrict which Google accounts can activate servers, limiting usage to an approved community for free tier cost control.

### `servers/{serverId}`
```json
{
  "sessionCode": "A7X9K2",
  "currentTrack": {
    "title": "...",
    "artist": "...",
    "url": "...",
    "thumbnail": "...",
    "duration": 245,
    "requestedBy": "username",
    "startedAt": "Timestamp"
  },
  "queue": [
    { "title": "...", "artist": "...", "url": "...", "thumbnail": "...", "duration": 245, "requestedBy": "username" }
  ],
  "isPlaying": false,
  "isPaused": false,
  "loopMode": "off",
  "volume": 80,
  "voiceChannelId": "...",
  "textChannelId": "...",
  "idleTimeoutMinutes": 5
}
```

### `servers/{serverId}/playlists/{playlistName}`
```json
{
  "name": "chill vibes",
  "tracks": [
    { "title": "...", "artist": "...", "url": "...", "thumbnail": "...", "duration": 245 }
  ],
  "createdBy": "username",
  "createdAt": "Timestamp"
}
```

### `servers/{serverId}/history/{sessionId}`
```json
{
  "startedAt": "Timestamp",
  "endedAt": "Timestamp",
  "tracks": [
    { "title": "...", "artist": "...", "url": "...", "thumbnail": "...", "playedAt": "Timestamp" }
  ]
}
```

### `sessionCodes/{code}`
```json
{
  "serverId": "...",
  "createdAt": "Timestamp"
}
```

Session code lookup: web user enters a code, resolves to a server ID, then subscribes to that server's playlist state via Firestore real-time listeners.

## Web App Features

**Tech stack:** React + Vite + TypeScript, hosted on Firebase Hosting.

### Entry Screen
- Two paths:
  1. **Server activation** — "Activate Your Server" button triggers Google login via Firebase Auth. After login, owner enters their Discord server ID to link the account. One-time setup.
  2. **Session access** — Session code input field + "Connect" button for playlist dashboard. No login required.
- Invalid/expired code shows an error message

### Dashboard (after valid code)
- **Now Playing** — track title, artist, thumbnail, progress bar (calculated from `startedAt` + `duration`)
- **Queue** — ordered list with drag-to-reorder, remove button per track, add-to-queue button
- **Search** — search bar querying YouTube via Firebase Cloud Function proxy. Spotify link paste resolves to track name. Results show as cards with "Add to Queue" button.
- **Playback Controls** — play/pause, skip, shuffle, loop toggle, volume slider. Writes directly to Firestore; bot picks up changes via listeners.
- **Saved Playlists** — view, load, save current queue, delete
- **History** — list of past sessions with track lists, option to re-queue an entire past session

### Cloud Function
One Firebase Cloud Function (Node.js/TypeScript) to proxy YouTube Data API v3 search requests. Keeps the API key server-side.

## GCP VM Setup

| Spec | Value |
|---|---|
| Machine type | e2-small (2 vCPU, 2GB RAM) |
| OS | Ubuntu 22.04 LTS |
| Disk | 20GB standard persistent disk |
| Region | us-central1 (adjust to user base) |
| Estimated cost | ~$13/mo |

### Docker Compose Services

```yaml
services:
  lavalink:
    image: ghcr.io/lavalink-devs/lavalink:4
    ports:
      - "2333:2333"
    # ~300-500MB RAM

  jacky-bot:
    build: ./bot
    depends_on:
      - lavalink
    env_file: .env
    # ~100-200MB RAM
    # Env: DISCORD_TOKEN, FIREBASE_CREDENTIALS, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
```

### Firewall Rules
- Allow inbound port 22 (SSH) only
- Lavalink (2333) stays internal — bot connects via localhost
- No public exposure of Lavalink

### Deployment
- **Bot/Lavalink:** SSH into VM, `git pull`, `docker compose up -d --build`
- **Web app:** `firebase deploy --only hosting`
- **Cloud Function:** `firebase deploy --only functions`
- Optional: GitHub Actions workflow for auto-deploy on push to main

## External APIs

| API | Usage | Cost |
|---|---|---|
| Spotify Web API | Resolve Spotify links to track metadata | Free |
| YouTube Data API v3 | Search from web app (via Cloud Function) | Free — 10,000 quota units/day (~100 searches) |
| Lavalink / Lavaplayer | YouTube audio extraction for playback | Free (open source, self-hosted) |
| Discord API | Bot connection and voice streaming | Free |
| Firebase Firestore | Playlist state and real-time sync | Free tier: 50K reads, 20K writes, 20K deletes per day |
| Firebase Hosting | Web app static hosting | Free tier: 10GB transfer/mo |
| Firebase Cloud Functions | YouTube search proxy | Free tier: 2M invocations/mo |

## Out of Scope

- Account whitelisting (planned for future — restrict which Google accounts can activate servers)
- Sharding / multi-node Lavalink
- Slash commands (prefix `j!` only)
- Audio sources beyond YouTube + Spotify link resolution
- Mobile-optimized responsive design (functional but not optimized)
