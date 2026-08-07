"""Stream Deck control API (spec: 2026-08-07-streamdeck-oauth-summon-design).

Mounted on the same aiohttp app as /health, so auth is a per-route wrapper,
NOT an app middleware — the guardian polls /health unauthenticated.

Auth: per-user bearer tokens (TokenStore, sha256 at rest). Identity derives
server-side from the token — the wire contract carries no discordUserId.
Rate limiting runs AFTER successful auth (Task 2 security review): invalid
tokens must never grow the limiter's key set.

Session resolution: the target guild is the first one where the token's user
is currently in a voice channel AND the bot holds a voice client (a live
session). Same liveness signal PlayerService.handle_summon uses.
"""

import hashlib
import logging
from typing import Any

from aiohttp import web

log = logging.getLogger("jacky.control")


def register_control_routes(
    app: web.Application, *, bot: Any, service: Any, token_store: Any, limiter: Any
) -> None:
    def guarded(handler):
        async def wrapper(request: web.Request) -> web.Response:
            supplied = request.headers.get("Authorization", "")
            if not supplied.startswith("Bearer "):
                return web.json_response({"error": "unauthorized"}, status=401)
            token = supplied[len("Bearer "):]
            user_id = await token_store.resolve(token)
            if user_id is None:
                return web.json_response({"error": "unauthorized"}, status=401)
            key = hashlib.sha256(token.encode()).hexdigest()
            if not limiter.allow(key):
                return web.json_response({"error": "rate-limited"}, status=429)
            return await handler(request, user_id)
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

    def member_id_of(user_id: str) -> int:
        # Discord ids are strings in JSON/Firestore (TokenStore stores them
        # as strings) but ints in discord.py's caches — convert at the edge.
        return int(user_id)

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

    async def now_playing(request: web.Request, user_id: str) -> web.Response:
        guild = resolve_guild(member_id_of(user_id))
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

    async def action_target(request: web.Request, user_id: str):
        """(guild, body, error_response) triple for POST action routes."""
        body = await body_of(request)
        guild = resolve_guild(member_id_of(user_id))
        if guild is None:
            return None, body, web.json_response(
                {"error": "no-active-session"}, status=409
            )
        return guild, body, None

    async def play_pause(request: web.Request, user_id: str) -> web.Response:
        guild, _body, err = await action_target(request, user_id)
        if err:
            return err
        # Read-then-write toggle: two overlapping presses can collapse into
        # one. Acceptable for a single-user personal API.
        state = await service.repo.get_state(str(guild.id)) or {}
        new_paused = not state.get("isPaused", False)
        await service.pause(guild.id, new_paused)
        return web.json_response({"paused": new_paused})

    async def skip(request: web.Request, user_id: str) -> web.Response:
        guild, _body, err = await action_target(request, user_id)
        if err:
            return err
        await service.skip(guild.id)
        return web.json_response({"ok": True})

    async def stop(request: web.Request, user_id: str) -> web.Response:
        guild, _body, err = await action_target(request, user_id)
        if err:
            return err
        await service.teardown_session(guild.id, clear_queue=True)
        return web.json_response({"ok": True})

    async def volume(request: web.Request, user_id: str) -> web.Response:
        guild, body, err = await action_target(request, user_id)
        if err:
            return err
        try:
            delta = int(body["delta"])
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "bad-delta"}, status=400)
        state = await service.repo.get_state(str(guild.id)) or {}
        new = await service.set_volume(guild.id, volume_of(state) + delta)
        return web.json_response({"volume": new})

    async def channels(request: web.Request, user_id: str) -> web.Response:
        # Cache-only membership check is acceptable here (spec §Decisions):
        # a cache miss only hides a guild from the PI dropdown — it never
        # grants access — and the PI refreshes after summon use.
        member_id = member_id_of(user_id)
        out = []
        for guild in bot.guilds:
            if not await service.repo.is_activated(str(guild.id)):
                continue
            if not guild.get_member(member_id):
                continue
            out.append({
                "guildId": str(guild.id),
                "guildName": guild.name,
                "channels": [
                    {"id": str(c.id), "name": c.name}
                    for c in guild.voice_channels
                ],
            })
        return web.json_response(out)

    app.add_routes([
        web.get("/control/now-playing", guarded(now_playing)),
        web.post("/control/play-pause", guarded(play_pause)),
        web.post("/control/skip", guarded(skip)),
        web.post("/control/stop", guarded(stop)),
        web.post("/control/volume", guarded(volume)),
        web.get("/control/channels", guarded(channels)),
    ])
    log.info("control API routes registered")
