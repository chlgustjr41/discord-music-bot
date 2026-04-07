# CLAUDE.md

## Project: Jacky Music (Discord Music Bot)

### Repository Structure
- `bot/` — Python Discord bot (discord.py + wavelink)
- `frontend/` — React + Vite + TypeScript web app
- `functions/` — Firebase Cloud Functions (YouTube search proxy)
- `lavalink/` — Lavalink server configuration

### Commands

#### Bot
```bash
cd bot
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
python main.py
```

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

#### Docker (bot + Lavalink)
```bash
docker compose up -d --build
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
See `.env.example` for all required variables.

### Design & Plan
- Design spec: `docs/superpowers/specs/2026-04-06-jacky-music-design.md`
- Implementation plan: `docs/superpowers/plans/2026-04-06-jacky-music-implementation.md`
