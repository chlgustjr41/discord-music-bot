"""Stream Deck control API (spec: 2026-08-06-streamdeck-session-control).

Mounted on the same aiohttp app as /health, so auth is a per-route wrapper,
NOT an app middleware — the guardian polls /health unauthenticated.

Session resolution: the caller sends their Discord user id; the target guild
is the first one where that member is currently in a voice channel AND the
bot holds a voice client (a live session). Same liveness signal
PlayerService.handle_summon uses.
"""

import hmac
import logging
from typing import Any

from aiohttp import web

log = logging.getLogger("jacky.control")


def register_control_routes(
    app: web.Application, *, bot: Any, service: Any, token: str
) -> None:
    if not token:
        raise ValueError("control API requires a non-empty token")
    expected = f"Bearer {token}".encode()

    def guarded(handler):
        async def wrapper(request: web.Request) -> web.Response:
            supplied = request.headers.get("Authorization", "").encode()
            if not hmac.compare_digest(supplied, expected):
                return web.json_response({"error": "unauthorized"}, status=401)
            return await handler(request)
        return wrapper

    def resolve_guild(user_id: int):
        for guild in bot.guilds:
            member = guild.get_member(user_id)
            voice = getattr(member, "voice", None)
            if member and voice and voice.channel and guild.voice_client:
                return guild
        return None

    def parse_user_id(raw) -> int | None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    async def body_of(request: web.Request) -> dict:
        try:
            return await request.json()
        except Exception:  # noqa: BLE001 — malformed body == empty body
            return {}

    async def now_playing(request: web.Request) -> web.Response:
        user_id = parse_user_id(request.query.get("discordUserId"))
        if user_id is None:
            return web.json_response({"error": "bad-discordUserId"}, status=400)
        guild = resolve_guild(user_id)
        if guild is None:
            return web.json_response({"active": False})
        state = await service.repo.get_state(str(guild.id)) or {}
        current = state.get("currentTrack")
        return web.json_response({
            "active": True,
            "title": current.get("title") if current else None,
            "author": current.get("artist", "") if current else "",
            "paused": bool(state.get("isPaused", False)),
            "volume": int(state.get("volume", 80)),
            "guildName": guild.name,
        })

    app.add_routes([web.get("/control/now-playing", guarded(now_playing))])
    log.info("control API routes registered")
