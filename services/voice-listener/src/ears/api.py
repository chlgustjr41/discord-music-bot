"""Inbound control API (bot -> listener) and outbound intent shipping."""

import asyncio
import logging
from typing import Any, Protocol

import aiohttp
from aiohttp import web

from ears.phrases import validate_phrase

log = logging.getLogger("ears.api")


class Gateway(Protocol):
    async def join(self, guild_id: str, channel_id: str, wake_phrase: str) -> None: ...
    async def leave(self, guild_id: str) -> None: ...
    def knows_word(self, word: str) -> bool: ...


def build_app(gateway: Gateway, internal_token: str) -> web.Application:
    @web.middleware
    async def auth(request: web.Request, handler):
        if request.path != "/health" and \
                request.headers.get("X-Voice-Token") != internal_token:
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    async def session(request: web.Request) -> web.Response:
        body = await request.json()
        if body.get("action") == "leave":
            await gateway.leave(body["guild_id"])
        else:
            await gateway.join(body["guild_id"], body["channel_id"],
                               body.get("wake_phrase") or "hey jacky")
        return web.json_response({"ok": True})

    async def validate(request: web.Request) -> web.Response:
        body = await request.json()
        problems = validate_phrase(body.get("phrase", ""), gateway.knows_word)
        return web.json_response({"ok": not problems, "problems": problems})

    async def health(_r: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app = web.Application(middlewares=[auth])
    app.add_routes([web.post("/session", session), web.post("/validate", validate),
                    web.get("/health", health)])
    return app


async def ship_intent(session: aiohttp.ClientSession, url: str, token: str,
                      guild_id: str, intent: Any) -> bool:
    """POST a recognized intent to the bot. Returns False on any failure
    (caller plays the error buzz; intents are fire-and-forget, never queued)."""
    try:
        async with session.post(url, json={
            "guild_id": guild_id, "intent": intent.name, "arg": intent.arg,
        }, headers={"X-Voice-Token": token},
                timeout=aiohttp.ClientTimeout(total=5)) as resp:
            return resp.status == 200
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        # TimeoutError (not a ClientError) is what ClientTimeout raises on a
        # connected-but-hung bot; must be soft too or the sink thread's ship
        # coroutine dies with an unretrieved exception instead of an error buzz.
        log.warning("intent ship failed: %s", exc)
        return False
