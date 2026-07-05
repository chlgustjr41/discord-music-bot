"""Owned Lavalink v4 client: REST for player control, WebSocket for events.

Replaces wavelink (ADR-0001). We own reconnect behavior: the WS loop retries
with capped exponential backoff forever, resuming the previous Lavalink
session when the server still remembers it (Lavalink v4 session resuming).
Consumers subscribe to events via the `on_*` callback attributes; every
callback is an async function invoked from the WS read loop.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import quote

import aiohttp

from jacky.audio.models import LoadResult

log = logging.getLogger("jacky.node")

RESUME_TIMEOUT_SECONDS = 60
_BACKOFF_START = 1.0
_BACKOFF_CAP = 30.0


class NodeError(RuntimeError):
    """A Lavalink REST call failed."""


class LavalinkNode:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        password: str,
        user_id: int,
        identifier: str = "vm-primary",
    ) -> None:
        self._session = session
        self._base = f"http://{host}:{port}/v4"
        self._ws_url = f"ws://{host}:{port}/v4/websocket"
        self._password = password
        self._user_id = user_id
        self.identifier = identifier
        self.session_id: str | None = None
        self.connected = False
        self._task: asyncio.Task | None = None
        self._first_ready = asyncio.Event()

        # Event callbacks (async). Set by the player service before start().
        self.on_ready: Callable[[bool], Awaitable[None]] | None = None
        self.on_player_update: Callable[[int, dict], Awaitable[None]] | None = None
        self.on_track_start: Callable[[int, dict], Awaitable[None]] | None = None
        self.on_track_end: Callable[[int, str], Awaitable[None]] | None = None
        self.on_track_exception: Callable[[int, dict], Awaitable[None]] | None = None
        self.on_voice_ws_closed: Callable[[int, dict], Awaitable[None]] | None = None
        self.on_disconnected: Callable[[], Awaitable[None]] | None = None

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def wait_ready(self, timeout: float = 60.0) -> None:
        await asyncio.wait_for(self._first_ready.wait(), timeout=timeout)

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.connected = False

    async def _run(self) -> None:
        backoff = _BACKOFF_START
        while True:
            try:
                await self._connect_and_read()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("node %s connection lost: %s", self.identifier, exc)
            was_connected = self.connected
            self.connected = False
            if was_connected and self.on_disconnected:
                await self.on_disconnected()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_CAP)
            log.info("node %s reconnecting...", self.identifier)

    async def _connect_and_read(self) -> None:
        headers = {
            "Authorization": self._password,
            "User-Id": str(self._user_id),
            "Client-Name": "jacky/2.0.0",
        }
        if self.session_id:
            headers["Session-Id"] = self.session_id
        async with self._session.ws_connect(self._ws_url, headers=headers, heartbeat=30) as ws:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                await self._dispatch(msg.json())
        raise ConnectionError("websocket closed by server")

    async def _dispatch(self, payload: dict) -> None:
        op = payload.get("op")
        if op == "ready":
            self.session_id = payload["sessionId"]
            resumed = bool(payload.get("resumed"))
            self.connected = True
            log.info(
                "node %s ready (session=%s resumed=%s)",
                self.identifier, self.session_id, resumed,
            )
            try:
                await self.configure_resuming(RESUME_TIMEOUT_SECONDS)
            except NodeError as exc:
                log.warning("configure resuming failed: %s", exc)
            self._first_ready.set()
            if self.on_ready:
                await self.on_ready(resumed)
        elif op == "playerUpdate":
            if self.on_player_update:
                await self.on_player_update(
                    int(payload["guildId"]), payload.get("state", {})
                )
        elif op == "event":
            await self._dispatch_event(payload)
        # op == "stats" is intentionally ignored.

    async def _dispatch_event(self, payload: dict) -> None:
        guild_id = int(payload["guildId"])
        event = payload.get("type")
        if event == "TrackStartEvent":
            if self.on_track_start:
                await self.on_track_start(guild_id, payload.get("track", {}))
        elif event == "TrackEndEvent":
            if self.on_track_end:
                await self.on_track_end(guild_id, payload.get("reason", "finished"))
        elif event in ("TrackExceptionEvent", "TrackStuckEvent"):
            if self.on_track_exception:
                await self.on_track_exception(guild_id, payload)
        elif event == "WebSocketClosedEvent":
            # Discord closed the voice WS (server migration, session
            # invalidation). Playback stalls silently unless someone reacts.
            if self.on_voice_ws_closed:
                await self.on_voice_ws_closed(guild_id, payload)

    # ── REST ─────────────────────────────────────────────────────────────

    async def _request(self, method: str, path: str, *, json: dict | None = None) -> dict | None:
        url = f"{self._base}{path}"
        async with self._session.request(
            method, url, json=json, headers={"Authorization": self._password}
        ) as resp:
            if resp.status == 204:
                return None
            body = await resp.json(content_type=None)
            if resp.status >= 400:
                message = (body or {}).get("message", "") if isinstance(body, dict) else ""
                raise NodeError(f"{method} {path} -> HTTP {resp.status} {message}")
            return body

    async def load_tracks(self, identifier: str) -> LoadResult:
        body = await self._request(
            "GET", f"/loadtracks?identifier={quote(identifier, safe='')}"
        )
        return LoadResult.from_response(body or {})

    async def update_player(
        self, guild_id: int, data: dict, *, no_replace: bool = False
    ) -> dict | None:
        if not self.session_id:
            raise NodeError("no Lavalink session yet")
        flag = "true" if no_replace else "false"
        return await self._request(
            "PATCH",
            f"/sessions/{self.session_id}/players/{guild_id}?noReplace={flag}",
            json=data,
        )

    async def destroy_player(self, guild_id: int) -> None:
        if not self.session_id:
            return
        try:
            await self._request("DELETE", f"/sessions/{self.session_id}/players/{guild_id}")
        except NodeError as exc:
            log.debug("destroy_player(%s): %s", guild_id, exc)

    async def configure_resuming(self, timeout: int) -> None:
        await self._request(
            "PATCH", f"/sessions/{self.session_id}", json={"resuming": True, "timeout": timeout}
        )

    async def get_players(self) -> list[dict]:
        body = await self._request("GET", f"/sessions/{self.session_id}/players")
        return body or []
