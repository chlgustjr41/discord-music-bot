# Jacky Music

Discord music bot with a companion web dashboard for real-time playlist
management. Users control playback from Discord (`j!` commands) or the web
app — both stay in sync through Firestore.

**Status: the v2 stability rewrite is in production** (GCP, since
2026-07-04). Current operational state: [docs/STATUS.md](docs/STATUS.md).

## Architecture

Five crash-only Docker Compose services on one VM. State lives in
Firestore and a shared token volume — never in containers — so **any
container can be killed at any instant** and the system converges back:
the bot rebuilds every live session from Firestore on startup and resumes
tracks at position.

```
                     Discord                YouTube
                        ▲                      ▲
                        │                      │ (poToken + OAuth + client order)
                    ┌───┴───┐   REST/WS   ┌────┴─────┐
                    │  bot  ├────────────►│ lavalink │◄─── poToken push
                    └───┬───┘             └────▲─────┘          │
       health ping      │        canary       │          ┌─────┴────────┐
      ┌───────────┐◄────┘◄────────────────────┘          │ token-minter │
      │ guardian  │                                      └─────▲────────┘
      └─────┬─────┘                                            │ mint
            ▼ restart / alert (Discord webhook)          ┌─────┴────────┐
       Docker socket                                     │ pot-provider │
                                                         └──────────────┘
```

| Service | Role |
|---------|------|
| `services/bot` | Discord commands, voice, playback orchestration. Owns its Lavalink client (no wavelink, [ADR-0001](docs/architecture/decisions/0001-owned-lavalink-client.md)); stateless ([ADR-0003](docs/architecture/decisions/0003-crash-only-state.md)); zero watchdog code |
| `services/lavalink` | Audio engine. Plugin version templated from `.env` (drift impossible); layered YouTube auth: client ordering + poToken + OAuth |
| `services/token-minter` | Refreshes poToken/visitorData every 5.5h via pot-provider, pushes to Lavalink at runtime, persists for cold starts ([ADR-0004](docs/architecture/decisions/0004-bgutil-pot-provider.md)) |
| `pot-provider` | Stock bgutil sidecar that solves YouTube's BotGuard attestation |
| `services/guardian` | The supervisor: canary probe every 2 min → classify failures (playbooks F1–F9) → auto-restart via Docker socket → Discord webhook alerts with the exact fix command |

**Docs:** [Architecture](docs/architecture/ARCHITECTURE.md) ·
[Runbook (F1–F9)](docs/operations/RUNBOOK.md) ·
[Deployment](docs/operations/DEPLOYMENT.md) ·
[Status](docs/STATUS.md) ·
[ADRs](docs/architecture/decisions/) ·
[Roadmap](docs/roadmap/FUTURE.md)

## Deployment

The contract on any Linux host with Docker:

```bash
git clone https://github.com/chlgustjr41/discord-music-bot.git && cd discord-music-bot
cp deploy/.env.example deploy/.env     # fill it — every variable documented inline
# place the Firebase service-account JSON at deploy/firebase-service-account.json
make up
```

Day-2 operations: `make help` lists everything —
`deploy` (pull + rebuild), `logs s=<svc>`, `restart s=<svc>`,
`reauth` (YouTube OAuth device flow, playbook F2), `test`, `lint`.

## Bot Commands

All commands use the `j!` prefix. A server must be activated once via the
web app (Google sign-in) before commands work.

| Playback | Queue | Playlists & Session |
|---|---|---|
| `j!play <query/URL>` — play or enqueue (YouTube/SoundCloud/Bandcamp; playlist URLs expand) | `j!queue [page]` — show queue | `j!playlist save/load/delete <name>` |
| `j!pause` / `j!resume` | `j!remove <pos>` | `j!playlist list` |
| `j!skip` — next track | `j!move <from> <to>` | `j!history` — recent sessions |
| `j!stop` — disconnect, end session | `j!shuffle` | `j!session` — show dashboard code |
| `j!volume <0-100>` · `j!loop` — off→track→queue | | `j!start` — join voice, mint a session code |
| `j!nowplaying` · `j!reset` — rebuild the voice session, queue preserved | | |

Local audio nodes (`j!localnode …`) are not part of v2 yet — see
[FUTURE.md](docs/roadmap/FUTURE.md); the `NodeProvider` seam for them
already exists.

## Web Dashboard

Joining voice generates a 6-character session code (also shown in the
bot's nickname). Anyone with the code can: see now-playing with live
progress and seek, control playback, search YouTube and queue tracks,
drag-to-reorder the queue, manage playlists, and view command/music
history. The dashboard talks only to Firestore; the bot's snapshot
listener translates doc changes into player actions.

## Development

```bash
# Unit tests + lint for all three Python services (92 tests)
make test && make lint

# Single service, editable install
cd services/bot && pip install -e ".[dev]" && pytest

# Frontend (React + Vite)
cd frontend && npm install && npm run dev

# Cloud Functions (YouTube search proxy)
cd functions && npm install && npm run build
```

CI runs ruff + pytest + image builds per service on every PR, plus a
compose-based integration smoke (Lavalink boot, synthetic + real token
injection, guardian boot). A daily scheduled run doubles as the live
YouTube mint canary.

## Repository Structure

```
discord-music-bot/
├── services/            # v2 production services (see table above)
│   ├── bot/src/jacky/   #   audio/ (node, player, voice) · state/ (repos, listener)
│   │                    #   commands/ (cogs) · core/ (bot wiring, health, runtime)
│   ├── guardian/src/guardian/  # probe / classify / act / alert / monitor / watcher
│   ├── token-minter/src/minter/
│   └── lavalink/        # application.yml.tmpl + entrypoint (env-templated)
├── deploy/              # docker-compose.yml + .env contract (secrets live on the host)
├── docs/                # architecture, ADRs, runbook, deployment, status, roadmap
├── frontend/            # React dashboard (Firebase Hosting)
├── functions/           # Cloud Functions (YouTube Data API search proxy)
├── scripts/             # reauth-v2.sh (OAuth device flow)
└── bot/                 # LEGACY v1 — kept only as rollback during the soak week
```

## Firestore Data Model

```
discord-music-bot (database)
├── sessionCodes/{code}          # session code → server mapping
├── servers/{serverId}           # live playback state (the bot↔dashboard contract)
│   ├── sessionCode, currentTrack, queue[], isPlaying, isPaused
│   ├── loopMode, volume, voiceChannelId, textChannelId, seekPosition
│   ├── searchQuery → searchResults[] (dashboard search round-trip)
│   └── playlists/{name} · musicHistory/ · commandHistory/ · history/
└── serverOwners/{serverId}      # activation records (isActive)
```

## License

Private project.
