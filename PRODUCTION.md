# Production Deployment

Complete deployment guide for Jacky Music on GCP + Firebase.

## Infrastructure Overview

| Component | Service | Detail |
|-----------|---------|--------|
| Bot + Lavalink | GCP e2-small VM | Docker Compose, 2 vCPU / 2GB RAM |
| Web App | Firebase Hosting | `discord-bot-jacky-music.web.app` |
| Cloud Functions | Firebase Functions | YouTube search proxy (Node.js 20) |
| Database | Firestore | Database name: `discord-music-bot` |
| Auth | Firebase Auth | Google sign-in provider |
| GCP Project | `personal-server-492701` | |
| VM Instance | `personal-project-machine` | |

### GCP VM Spec

| Spec | Value |
|------|-------|
| Machine type | e2-small (2 vCPU, 2GB RAM) |
| OS | Ubuntu 22.04 LTS |
| Disk | 20GB standard persistent |
| Region | (configure per preference) |
| Estimated cost | ~$13/month |
| Firewall | Allow TCP 2333 (Lavalink, internal only) |

## Initial VM Setup

The easiest path is `./deploy/gcp-deploy.sh`, which creates the VM on first
run, uploads the repo + `.env`, and runs `docker compose up -d --build`.

Manual steps below if you need to do it by hand.

### 1. Create VM

```bash
gcloud compute instances create personal-project-machine \
  --project=personal-server-492701 \
  --machine-type=e2-small \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=20GB \
  --tags=jacky-bot
```

### 2. Install Docker

SSH into the VM and run the startup script:

```bash
gcloud compute ssh personal-project-machine --project=personal-server-492701
sudo bash deploy/startup.sh
```

Or manually:

```bash
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update -y
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable docker && sudo systemctl start docker
```

### 3. Clone and configure

```bash
git clone https://github.com/chlgustjr41/discord-music-bot.git
cd discord-music-bot
cp .env.example .env
nano .env  # Fill in all values
```

Place the Firebase service account key JSON on the VM (e.g., `./bot/serviceAccountKey.json`) and set `FIREBASE_SERVICE_ACCOUNT_KEY` in `.env` accordingly.

### 4. Start services

```bash
docker compose up -d --build
```

This starts:
- **lavalink** — Audio server on port 2333 (512MB heap)
- **jacky-bot** — Discord bot (depends on lavalink)

## Deploy Bot (Update)

SSH into the VM:

```bash
cd discord-music-bot
git pull origin master
docker compose up -d --build
```

To restart without rebuilding:

```bash
docker compose restart jacky-bot
```

To view logs:

```bash
docker compose logs -f jacky-bot
docker compose logs -f lavalink
```

## Deploy Web App

From local machine:

```bash
cd frontend
npm run build
cd ..
npx firebase deploy --only hosting
```

Live at: https://discord-bot-jacky-music.web.app

## Deploy Cloud Functions

```bash
cd functions
npm run build
cd ..
npx firebase deploy --only functions
```

## Deploy Firestore Rules

```bash
npx firebase deploy --only firestore:rules
```

## Environment Variables

### Bot (`.env` on VM)

| Variable | Description |
|----------|-------------|
| `DISCORD_TOKEN` | Bot token from Discord Developer Portal |
| `FIREBASE_PROJECT_ID` | `discord-bot-jacky-music` |
| `FIREBASE_SERVICE_ACCOUNT_KEY` | Path to service account JSON |
| `LAVALINK_HOST` | `lavalink` (Docker service name) |
| `LAVALINK_PORT` | `2333` |
| `LAVALINK_PASSWORD` | `youshallnotpass` (match `lavalink/application.yml`) |
| `YOUTUBE_API_KEY` | YouTube Data API v3 key |
| `WEB_APP_URL` | `https://discord-bot-jacky-music.web.app` |

### Frontend (`frontend/.env`)

| Variable | Description |
|----------|-------------|
| `VITE_FIREBASE_API_KEY` | Firebase web API key |
| `VITE_FIREBASE_AUTH_DOMAIN` | `discord-bot-jacky-music.firebaseapp.com` |
| `VITE_FIREBASE_PROJECT_ID` | `discord-bot-jacky-music` |
| `VITE_FIREBASE_STORAGE_BUCKET` | Firebase storage bucket |
| `VITE_FIREBASE_MESSAGING_SENDER_ID` | Firebase sender ID |
| `VITE_FIREBASE_APP_ID` | Firebase app ID |

## Docker Compose Services

```yaml
services:
  lavalink:
    image: ghcr.io/lavalink-devs/lavalink:4
    restart: unless-stopped
    ports: ["2333:2333"]
    environment: _JAVA_OPTIONS=-Xmx512m

  jacky-bot:
    build: ./bot
    restart: unless-stopped
    depends_on: [lavalink]
    env_file: [.env]
    environment:
      LAVALINK_HOST: lavalink   # Override to use Docker DNS
```

Both containers have `restart: unless-stopped` so they recover from crashes and VM reboots.

## Lavalink Configuration

Key settings in `lavalink/application.yml`:

| Setting | Value | Notes |
|---------|-------|-------|
| Password | `youshallnotpass` | Must match bot `.env` |
| YouTube plugin | v1.18.0 | Handles search and playback |
| YouTube clients | MUSIC, ANDROID_MUSIC, ANDROID_VR | WEB excluded (sig extraction breaks) |
| Buffer | 400ms player, 5000ms frame | |
| Java heap | 512MB (`-Xmx512m`) | Set in docker-compose.yml |
| Sources | YouTube, Bandcamp, SoundCloud, Twitch, Vimeo, HTTP | |

## Firestore Security Rules

```
sessionCodes/{code}     — read: anyone, write: bot only (admin SDK)
servers/{serverId}       — read/write: anyone (session code is the gate)
  /{subcollection}/*     — read/write: anyone
serverOwners/{serverId}  — read: anyone, write: authenticated users
```

Session codes (6-character alphanumeric) serve as the access control layer. A new code is generated each time the bot joins voice, invalidating the previous one.

## Monitoring

### Check service health

```bash
docker compose ps                    # Container status
docker compose logs -f jacky-bot     # Bot logs
docker compose logs -f lavalink      # Lavalink logs
docker stats                         # CPU/memory usage
```

### Common issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| Bot connects but no audio | Lavalink not ready | `docker compose restart lavalink`, wait 10s |
| "Track failed to load" | YouTube plugin client blocked | Update youtube-plugin version in `application.yml` |
| Web app can't find session | Session code expired | Bot generates new code on each voice join |
| High memory on Lavalink | Java heap growth | Restart: `docker compose restart lavalink` |
| Bot not responding to commands | Server not activated | Owner must sign in at `/activate` on web app |

### Restart everything

```bash
docker compose down
docker compose up -d --build
```

## Local Audio Node (Optional)

Users can run a local Lavalink audio node for lower latency by streaming audio from their own machine instead of the GCP server.

### How It Works

```
User's Machine                         Cloud (GCP)
┌──────────────┐   Cloudflare    ┌─────────────────┐
│  Lavalink    │◄──  Tunnel  ───►│  Jacky Music Bot │
│  (audio)     │                 │  (commands)       │
└──────┬───────┘                 └─────────────────┘
       │ UDP audio
       ▼
  Discord Voice
```

The bot stays on GCP handling commands. Only the audio engine runs locally, so audio takes a shorter network path to Discord's voice servers.

### Setup

Users clone the public repo [jacky-music-local](https://github.com/chlgustjr41/jacky-music-local) and run the setup script. It starts:
- **Lavalink** — audio server (Docker)
- **Cloudflare Tunnel** — exposes Lavalink securely with no port forwarding
- **Watchdog** — auto-shuts down after 15 min of no music

The setup script prints a tunnel URL and password. Users paste `j!localnode connect <url> <password>` in Discord.

### Failover

The bot health-checks local nodes every 15 seconds. If a local node goes down (e.g., watchdog shutdown, Docker stop), the bot automatically migrates the player back to the GCP node and notifies the text channel.

### Firestore

Local node connection state is stored in `serverOwners/{serverId}.localNode`:
```json
{
  "url": "https://abc123.trycloudflare.com",
  "password": "...",
  "connectedAt": "<timestamp>"
}
```

Cleared automatically on disconnect or failover.

## Costs

| Service | Monthly Cost |
|---------|-------------|
| GCP e2-small VM | ~$13 |
| Firestore | Free tier (50K reads/20K writes per day) |
| Firebase Hosting | Free tier (10GB transfer/month) |
| Firebase Auth | Free (Google sign-in) |
| Cloud Functions | Free tier (2M invocations/month) |
| YouTube Data API | Free tier (10,000 units/day) |
| **Total** | **~$13/month** |
