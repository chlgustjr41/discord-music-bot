"""Health endpoint for the guardian (playbooks F5, F6).

GET /health -> 200 {"status": "ok", "guilds": N, "players": {gid: state},
                    "audio": {...} | null}

`players` carries each guild's last Lavalink playerUpdate (position/connected,
plus `ping` when Lavalink reported one) and Firestore-cached isPlaying, so the
guardian can detect frozen positions without a Firestore dependency of its
own. `audio` is the latest Lavalink frame-statistics sample (audio/stats.py),
or null before the first one arrives.

The guardian consumes this: fields are only ever ADDED here, never renamed
or removed.
"""

import logging
import time
from typing import Any

from aiohttp import web

log = logging.getLogger("jacky.health")


def build_app(bot: Any, service: Any) -> web.Application:
    async def health(_request: web.Request) -> web.Response:
        players = {}
        for guild_id, pos in service.positions.items():
            players[str(guild_id)] = {
                "position": pos.get("position", 0),
                "connected": pos.get("connected", False),
                "time": pos.get("time", 0),
                "playing": service.playing.get(guild_id, False),
                "trackId": service.track_ids.get(guild_id),
            }
            # Round trip to Discord's voice server, in ms (-1 while not
            # connected). Absent rather than null when Lavalink sent none.
            if "ping" in pos:
                players[str(guild_id)]["ping"] = pos["ping"]
        audio = service.audio_stats
        return web.json_response({
            "status": "ok" if bot.is_ready() else "starting",
            "guilds": len(bot.guilds),
            "players": players,
            "audio": audio.as_health(now=time.monotonic()) if audio else None,
        })

    app = web.Application()
    app.add_routes([web.get("/health", health)])
    return app


async def start_health_server(
    bot: Any, service: Any, port: int, app: web.Application | None = None,
    host: str = "0.0.0.0",
) -> web.AppRunner:
    # No access log. The OAuth routes are mounted on this same app, and
    # aiohttp's default access log writes the whole request line — which for
    # /control/auth/callback and /control/auth/poll includes `state` (the
    # bearer for claiming a freshly minted token) and `code`. Nothing else
    # reads the log: the guardian's /health poll would be its only other line.
    runner = web.AppRunner(app or build_app(bot, service), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("health endpoint listening on :%d", port)
    return runner
