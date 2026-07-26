"""Health endpoint for the guardian (playbooks F5, F6).

GET /health -> 200 {"status": "ok", "guilds": N, "players": {gid: state}}
`players` carries each guild's last Lavalink playerUpdate (position/connected)
plus Firestore-cached isPlaying, so the guardian can detect frozen positions
without a Firestore dependency of its own.
"""

import logging
from typing import Any

from aiohttp import web

log = logging.getLogger("jacky.health")


def build_app(bot: Any, service: Any, dispatcher: Any = None,
              voice_token: str = "") -> web.Application:
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
        return web.json_response({
            "status": "ok" if bot.is_ready() else "starting",
            "guilds": len(bot.guilds),
            "players": players,
        })

    async def voice_intent(request: web.Request) -> web.Response:
        if dispatcher is None:
            return web.json_response({"error": "voice control disabled"}, status=404)
        if request.headers.get("X-Voice-Token") != voice_token:
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await request.json()
        ok = await dispatcher.dispatch(
            int(body["guild_id"]), body.get("intent", ""), body.get("arg")
        )
        log.info("voice intent %s(%s) guild=%s -> %s",
                 body.get("intent"), body.get("arg"), body.get("guild_id"), ok)
        return web.json_response({"ok": ok}, status=200 if ok else 422)

    app = web.Application()
    app.add_routes([web.get("/health", health)])
    app.add_routes([web.post("/voice-intent", voice_intent)])
    return app


async def start_health_server(bot: Any, service: Any, port: int,
                              dispatcher: Any = None,
                              voice_token: str = "") -> web.AppRunner:
    runner = web.AppRunner(build_app(bot, service, dispatcher, voice_token))
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("health endpoint listening on :%d", port)
    return runner
