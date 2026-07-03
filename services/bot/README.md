# jacky-bot

**What:** Discord-facing service — slash commands, voice, playback
orchestration. Zero recovery logic (guardian's job, ADR-0003).

**Run:** `pip install -e ".[dev]" && python -m jacky` · Tests: `pytest`

**Depends on:** Lavalink (REST/WS, env: LAVALINK_HOST/PORT/PASSWORD),
Firestore (source of truth), Discord gateway (DISCORD_TOKEN).
Layout: `commands/` Discord handlers · `audio/` owned Lavalink client +
NodeProvider · `state/` Firestore repositories · `core/` config/lifecycle.
