"""LavalinkNode against a fake Lavalink v4 server (real HTTP + WebSocket).

Encodes the wire contract we depend on: ready/resume handshake, event
dispatch, REST auth, and reconnect-with-Session-Id after a dropped socket.
"""

import asyncio
import json

import aiohttp
import pytest
from aiohttp import web

from jacky.audio.node import LavalinkNode, NodeError
from tests.conftest import make_track

PASSWORD = "hunter2"


class FakeLavalink:
    def __init__(self) -> None:
        self.ws_connections: list[dict] = []   # headers per connection
        self.player_updates: list[tuple[str, dict, str]] = []
        self.resume_configs: list[dict] = []
        self.session_counter = 0
        self.active_ws: web.WebSocketResponse | None = None
        self.resumed_next = False

    def app(self) -> web.Application:
        app = web.Application()
        app.add_routes([
            web.get("/v4/websocket", self.websocket),
            web.get("/v4/loadtracks", self.loadtracks),
            web.patch("/v4/sessions/{sid}", self.configure_session),
            web.patch("/v4/sessions/{sid}/players/{gid}", self.update_player),
            web.delete("/v4/sessions/{sid}/players/{gid}", self.destroy_player),
        ])
        return app

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws_connections.append(dict(request.headers))
        self.active_ws = ws
        resumed = self.resumed_next and request.headers.get("Session-Id")
        if not resumed:
            self.session_counter += 1
        await ws.send_json({
            "op": "ready",
            "sessionId": f"sess-{self.session_counter}",
            "resumed": bool(resumed),
        })
        async for _msg in ws:
            pass
        return ws

    async def send_event(self, payload: dict) -> None:
        await self.active_ws.send_json(payload)

    async def loadtracks(self, request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != PASSWORD:
            return web.json_response({"message": "unauthorized"}, status=401)
        identifier = request.query["identifier"]
        if identifier.startswith("ytsearch:"):
            return web.json_response({"loadType": "search", "data": [make_track()]})
        return web.json_response({"loadType": "track", "data": make_track()})

    async def configure_session(self, request: web.Request) -> web.Response:
        self.resume_configs.append(await request.json())
        return web.json_response({"resuming": True, "timeout": 60})

    async def update_player(self, request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != PASSWORD:
            return web.json_response({"message": "unauthorized"}, status=401)
        body = await request.json()
        self.player_updates.append(
            (request.match_info["gid"], body, request.match_info["sid"])
        )
        return web.json_response({})

    async def destroy_player(self, request: web.Request) -> web.Response:
        return web.Response(status=204)


@pytest.fixture
async def stack():
    fake = FakeLavalink()
    runner = web.AppRunner(fake.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    session = aiohttp.ClientSession()
    node = LavalinkNode(session, "127.0.0.1", port, PASSWORD, user_id=999)
    yield fake, node
    await node.close()
    await session.close()
    await runner.cleanup()


async def test_connects_ready_and_configures_resuming(stack):
    fake, node = stack
    node.start()
    await node.wait_ready(timeout=5)

    assert node.connected and node.session_id == "sess-1"
    headers = fake.ws_connections[0]
    assert headers["Authorization"] == PASSWORD
    assert headers["User-Id"] == "999"
    assert fake.resume_configs == [{"resuming": True, "timeout": 60}]


async def test_events_dispatch_to_callbacks(stack):
    fake, node = stack
    seen: dict = {}

    async def on_track_end(gid, reason):
        seen["end"] = (gid, reason)

    async def on_player_update(gid, state):
        seen["update"] = (gid, state)

    node.on_track_end = on_track_end
    node.on_player_update = on_player_update
    node.start()
    await node.wait_ready(timeout=5)

    await fake.send_event({
        "op": "event", "type": "TrackEndEvent", "guildId": "123", "reason": "finished",
    })
    await fake.send_event({
        "op": "playerUpdate", "guildId": "123",
        "state": {"position": 5000, "connected": True, "time": 1},
    })
    await asyncio.sleep(0.1)
    assert seen["end"] == (123, "finished")
    assert seen["update"][1]["position"] == 5000


async def test_rest_load_and_update_player(stack):
    fake, node = stack
    node.start()
    await node.wait_ready(timeout=5)

    result = await node.load_tracks("ytsearch:hello world")
    assert result.kind == "search" and result.first["encoded"] == "ENC1"

    await node.update_player(123, {"track": {"encoded": "ENC1"}, "volume": 80})
    gid, body, sid = fake.player_updates[0]
    assert gid == "123" and body["volume"] == 80 and sid == "sess-1"


async def test_rest_error_raises_node_error(stack):
    fake, node = stack
    node.start()
    await node.wait_ready(timeout=5)
    node._password = "wrong"
    with pytest.raises(NodeError, match="401"):
        await node.load_tracks("ytsearch:x")


async def test_reconnects_with_session_id_and_reports_resumed(stack):
    fake, node = stack
    resumed_flags: list[bool] = []
    disconnects: list[bool] = []

    async def on_ready(resumed):
        resumed_flags.append(resumed)

    async def on_disconnected():
        disconnects.append(True)

    node.on_ready = on_ready
    node.on_disconnected = on_disconnected
    node.start()
    await node.wait_ready(timeout=5)

    fake.resumed_next = True
    await fake.active_ws.close()  # server drops the socket

    for _ in range(80):  # backoff starts at 1s
        await asyncio.sleep(0.1)
        if len(resumed_flags) == 2:
            break
    assert resumed_flags == [False, True]
    assert disconnects == [True]
    assert fake.ws_connections[1]["Session-Id"] == "sess-1"
    assert node.session_id == "sess-1"


async def test_websocket_closed_event_dispatches(stack):
    fake, node = stack
    seen: dict = {}

    async def on_voice_ws_closed(gid, payload):
        seen["closed"] = (gid, payload.get("code"))

    node.on_voice_ws_closed = on_voice_ws_closed
    node.start()
    await node.wait_ready(timeout=5)
    await fake.send_event({
        "op": "event", "type": "WebSocketClosedEvent", "guildId": "123",
        "code": 4014, "reason": "Disconnected.", "byRemote": True,
    })
    await asyncio.sleep(0.1)
    assert seen["closed"] == (123, 4014)


async def test_json_event_parsing_ignores_stats(stack):
    fake, node = stack
    node.start()
    await node.wait_ready(timeout=5)
    await fake.send_event({"op": "stats", "players": 0})
    await asyncio.sleep(0.05)
    assert node.connected  # nothing blew up


def test_ready_payload_shape_is_json_serializable():
    payload = {"op": "ready", "sessionId": "s", "resumed": False}
    assert json.loads(json.dumps(payload)) == payload
