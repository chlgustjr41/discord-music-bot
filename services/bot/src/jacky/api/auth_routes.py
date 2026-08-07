"""Discord OAuth sign-in routes (spec: 2026-08-07-streamdeck-oauth-summon).

Pending-state lifecycle: POST /control/auth/start mints a `state`; the browser
completes Discord OAuth at GET /control/auth/callback (which mints the control
token via TokenStore); the plugin then claims the token exactly once via
GET /control/auth/poll. States expire STATE_TTL_S after creation and are swept
lazily on every request.

These routes are intentionally NOT bearer-guarded — they are how a client
obtains a bearer. Start is rate-limited per client IP instead.
"""

import logging
import secrets
import time
from typing import Any

from aiohttp import web

from jacky.api.oauth import OAuthError
from jacky.api.ratelimit import SlidingWindow

log = logging.getLogger("jacky.control.auth")

STATE_TTL_S = 600.0
START_LIMIT = 10
START_WINDOW_S = 60.0

# Dark background + coral accent to match the Stream Deck plugin's look.
_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title></head>
<body style="margin:0;min-height:100vh;display:flex;align-items:center;\
justify-content:center;background:#1a1a2e;color:#eee;\
font-family:system-ui,sans-serif;text-align:center">
<div><h1 style="color:#e94560;font-size:1.6rem;margin:0 0 .5rem">{title}</h1>
<p style="font-size:1rem;color:#bbb;margin:0">{detail}</p></div>
</body></html>"""


def _page(status: int, title: str, detail: str) -> web.Response:
    return web.Response(
        text=_PAGE.format(title=title, detail=detail),
        status=status,
        content_type="text/html",
    )


def register_auth_routes(
    app: web.Application, *, oauth: Any, token_store: Any, member_gate: Any
) -> None:
    """Mount /control/auth/{start,callback,poll}.

    member_gate: async (user_id: str) -> bool — whether the user belongs to
    at least one activated guild (built in core/bot.py wiring).
    """
    pending: dict[str, dict] = {}
    limiter = SlidingWindow(limit=START_LIMIT, window_s=START_WINDOW_S)

    def sweep() -> None:
        now = time.monotonic()
        expired = [
            s for s, e in pending.items() if now - e["createdAt"] > STATE_TTL_S
        ]
        for s in expired:
            del pending[s]

    async def auth_start(request: web.Request) -> web.Response:
        if not limiter.allow(request.remote or "unknown"):
            return web.json_response({"error": "rate-limited"}, status=429)
        sweep()
        state = secrets.token_urlsafe(32)
        pending[state] = {"createdAt": time.monotonic()}
        return web.json_response(
            {"state": state, "authorizeUrl": oauth.authorize_url(state)}
        )

    async def auth_callback(request: web.Request) -> web.Response:
        sweep()
        state = request.query.get("state", "")
        code = request.query.get("code", "")
        entry = pending.get(state)
        if entry is None or not code:
            return _page(
                410, "Sign-in link expired",
                "Go back to the Stream Deck and start the sign-in again.",
            )
        try:
            access_token = await oauth.exchange_code(code)
            identity = await oauth.fetch_identity(access_token)
        except OAuthError:
            log.exception("discord oauth exchange failed")
            return _page(
                502, "Discord sign-in failed",
                "Something went wrong talking to Discord. Please try again.",
            )
        if not await member_gate(identity["id"]):
            entry["failed"] = "not-a-member"
            return _page(
                403, "Not a member",
                "This Discord account isn't in any server Jacky Music serves.",
            )
        token = await token_store.mint(identity["id"], identity["username"])
        entry.update({
            "token": token,
            "userId": identity["id"],
            "userName": identity["username"],
        })
        return _page(200, "Signed in", "You can close this tab.")

    async def auth_poll(request: web.Request) -> web.Response:
        sweep()
        state = request.query.get("state", "")
        entry = pending.get(state)
        if entry is None:
            return web.json_response({"error": "unknown-state"}, status=410)
        if "failed" in entry:
            return web.json_response({"error": entry["failed"]}, status=403)
        if "token" not in entry:
            return web.json_response({"status": "pending"}, status=202)
        del pending[state]  # one-time claim
        return web.json_response({
            "token": entry["token"],
            "discordUserId": entry["userId"],
            "discordUserName": entry["userName"],
        })

    app.add_routes([
        web.post("/control/auth/start", auth_start),
        web.get("/control/auth/callback", auth_callback),
        web.get("/control/auth/poll", auth_poll),
    ])
    log.info("auth routes registered")
