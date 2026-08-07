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
        # Relies on discord.py's member cache, which is populated for
        # voice-connected members via the voice_states intent (core/bot.py).
        # A user not in voice is simply absent -> resolves to no session.
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

    def volume_of(state: dict) -> int:
        # None-based default: 0 is a legal volume (j!volume 0 mutes) and must
        # not be conflated with "unset" (web app can write volume: null).
        vol = state.get("volume")
        return 80 if vol is None else int(vol)

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
            "volume": volume_of(state),
            "guildName": guild.name,
        })

    async def action_target(request: web.Request):
        """(guild, body, error_response) triple for POST action routes."""
        body = await body_of(request)
        user_id = parse_user_id(body.get("discordUserId"))
        if user_id is None:
            return None, body, web.json_response(
                {"error": "bad-discordUserId"}, status=400
            )
        guild = resolve_guild(user_id)
        if guild is None:
            return None, body, web.json_response(
                {"error": "no-active-session"}, status=409
            )
        return guild, body, None

    async def play_pause(request: web.Request) -> web.Response:
        guild, _body, err = await action_target(request)
        if err:
            return err
        # Read-then-write toggle: two overlapping presses can collapse into
        # one. Acceptable for a single-user personal API.
        state = await service.repo.get_state(str(guild.id)) or {}
        new_paused = not state.get("isPaused", False)
        await service.pause(guild.id, new_paused)
        return web.json_response({"paused": new_paused})

    async def skip(request: web.Request) -> web.Response:
        guild, _body, err = await action_target(request)
        if err:
            return err
        await service.skip(guild.id)
        return web.json_response({"ok": True})

    async def stop(request: web.Request) -> web.Response:
        guild, _body, err = await action_target(request)
        if err:
            return err
        await service.teardown_session(guild.id, clear_queue=True)
        return web.json_response({"ok": True})

    async def volume(request: web.Request) -> web.Response:
        guild, body, err = await action_target(request)
        if err:
            return err
        try:
            delta = int(body["delta"])
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "bad-delta"}, status=400)
        state = await service.repo.get_state(str(guild.id)) or {}
        new = await service.set_volume(guild.id, volume_of(state) + delta)
        return web.json_response({"volume": new})

    app.add_routes([
        web.get("/control/now-playing", guarded(now_playing)),
        web.post("/control/play-pause", guarded(play_pause)),
        web.post("/control/skip", guarded(skip)),
        web.post("/control/stop", guarded(stop)),
        web.post("/control/volume", guarded(volume)),
    ])
    log.info("control API routes registered")
