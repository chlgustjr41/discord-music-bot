# CLAUDE.md

## Project: Jacky Music (Discord Music Bot)

### Repository Structure
- `services/bot/` — Python Discord bot v2 (`src/jacky/`): owned Lavalink client, Firestore state, j! commands
- `services/guardian/` — supervisor: probe/classify/act/alert (playbooks F1–F9)
- `services/token-minter/` — poToken refresh via pot-provider sidecar (ADR-0004)
- `services/lavalink/` — templated Lavalink config
- `deploy/` — docker-compose.yml + .env contract
- `streamdeck-plugin/` — Stream Deck plugin (TypeScript, @elgato/streamdeck): j! session control keys via the bot's /control API (docs/streamdeck-control.md)
- `docs/` — architecture, ADRs, runbook, deployment, roadmap
- `bot/` — LEGACY v1 bot (replaced in production 2026-07-04; rollback-only during soak week, then archived — see docs/STATUS.md)
- `frontend/`, `functions/` — unchanged (web app + search proxy)

### Commands (v2)
- `make help` — list everything
- `make test` / `make lint` — pytest + ruff over services/
- `make up` / `make logs s=<svc>` / `make restart s=<svc>` — stack ops
- Spec: `docs/superpowers/specs/2026-07-02-stability-rewrite-design.md`

#### Frontend
```bash
cd frontend
npm install
npm run dev        # Vite dev server (port 5173)
npm run build      # Production build
npm run preview    # Preview production build
```

#### Cloud Functions
```bash
cd functions
npm install
npm run build
firebase deploy --only functions
```

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
v2 stack: see `deploy/.env.example` (every variable documented inline).
Legacy v1 bot: see root `.env.example`.

### Design & Plan
- Design spec: `docs/superpowers/specs/2026-07-02-stability-rewrite-design.md`
