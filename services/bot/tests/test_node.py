"""LavalinkNode against a fake Lavalink v4 server (real HTTP + WebSocket).

Encodes the wire contract we depend on: ready/resume handshake, event
dispatch, REST auth, and reconnect-with-Session-Id after a dropped socket.
"""

import asyncio
import json
import time

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
        self.load_requests: list[str] = []     # identifiers that arrived
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
        self.load_requests.append(identifier)
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


@pytest.mark.parametrize("link", [
    "http://192.168.1.1/",
    "http://169.254.169.254/latest",
    "http://localhost:2333/v4/info",
    "https://youtube.com@evil.test/x",
    "https://user@www.youtube.com/watch?v=x",
    "https://www.youtube.com:8443/watch?v=x",
    "file:///etc/passwd",
])
async def test_load_tracks_never_sends_a_link_outside_the_allowlist(stack, link):
    """The node is the only door to /loadtracks, so it refuses on its own
    account — a caller that skipped to_identifier still cannot make Lavalink
    fetch an arbitrary URL. Asserted on what ARRIVED at the server."""
    fake, node = stack
    node.start()
    await node.wait_ready(timeout=5)

    result = await node.load_tracks(link)

    assert result.unsupported and result.tracks == []
    assert fake.load_requests == []


async def test_load_tracks_sends_allowed_links_in_canonical_form(stack):
    fake, node = stack
    node.start()
    await node.wait_ready(timeout=5)

    await node.load_tracks("HTTPS://WWW.YOUTUBE.COM:443/watch?v=AbC")
    await node.load_tracks("https://soundcloud.com/a/b")
    await node.load_tracks("ytsearch:that song from http://192.168.1.1/")

    assert fake.load_requests == [
        "https://www.youtube.com/watch?v=AbC",
        "https://soundcloud.com/a/b",
        # Text with a link in the middle is a search, as it always was.
        "ytsearch:that song from http://192.168.1.1/",
    ]


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


# ── stats op: frame statistics are kept, not discarded ───────────────────

STATS_OP = {
    "op": "stats",
    "players": 2,
    "playingPlayers": 1,
    "uptime": 123456,
    "memory": {"free": 1, "used": 2, "allocated": 3, "reservable": 4},
    "cpu": {"cores": 4, "systemLoad": 0.12, "lavalinkLoad": 0.04},
    "frameStats": {"sent": 2877, "nulled": 120, "deficit": 3},
}


async def test_stats_op_becomes_the_latest_sample_and_reaches_on_stats(stack):
    fake, node = stack
    seen: list = []

    async def on_stats(sample):
        seen.append(sample)

    node.on_stats = on_stats
    node.start()
    await node.wait_ready(timeout=5)
    assert node.stats is None  # nothing received yet

    before = time.monotonic()
    await fake.send_event(STATS_OP)
    await asyncio.sleep(0.1)
    after = time.monotonic()

    stats = node.stats
    assert (stats.sent, stats.nulled, stats.deficit) == (2877, 120, 3)
    assert stats.playing_players == 1
    assert (stats.lavalink_load, stats.system_load) == (0.04, 0.12)
    # Monotonic receive time: staleness must survive a wall-clock change.
    assert before <= stats.received_at <= after
    assert seen == [stats]
    # Only the fields the frame-loss question needs are kept.
    assert not hasattr(stats, "memory") and not hasattr(stats, "uptime")


async def test_stats_is_read_only_from_outside(stack):
    _fake, node = stack
    with pytest.raises(AttributeError):
        node.stats = None


async def test_a_stats_op_without_frame_stats_replaces_the_previous_sample(stack):
    """Idle is news too: keeping the last LOSSY sample around after playback
    stops would report a problem that is no longer happening."""
    fake, node = stack
    node.start()
    await node.wait_ready(timeout=5)

    await fake.send_event(STATS_OP)
    await fake.send_event({**STATS_OP, "playingPlayers": 0, "frameStats": None})
    await asyncio.sleep(0.1)

    assert node.stats.playing_players == 0
    assert not node.stats.has_frames


async def test_stats_trouble_never_costs_the_lavalink_connection(stack):
    """An exception escaping _dispatch tears down the WebSocket that track
    events arrive on. Telemetry must not be able to do that — neither a
    malformed stats frame nor a consumer that raises."""
    fake, node = stack
    updates: list = []

    async def on_player_update(gid, state):
        updates.append(gid)

    async def on_stats(_sample):
        raise RuntimeError("consumer bug")

    node.on_player_update = on_player_update
    node.on_stats = on_stats
    node.start()
    await node.wait_ready(timeout=5)

    await fake.send_event({"op": "stats", "frameStats": "junk", "cpu": 5,
                           "playingPlayers": {"a": 1}})
    await fake.send_event({"op": "stats"})
    await fake.send_event(STATS_OP)
    await fake.send_event({"op": "playerUpdate", "guildId": "123", "state": {}})
    await asyncio.sleep(0.1)

    assert updates == [123]                 # the loop is still reading
    assert node.connected and len(fake.ws_connections) == 1   # no reconnect
    assert node.stats.sent == 2877          # the good sample still landed


def test_ready_payload_shape_is_json_serializable():
    payload = {"op": "ready", "sessionId": "s", "resumed": False}
    assert json.loads(json.dumps(payload)) == payload


async def test_a_raising_event_handler_never_costs_the_lavalink_connection(stack):
    """Track events are handled by code that reads client-writable documents.
    If a handler raises and the exception escapes _dispatch, the WebSocket is
    torn down and every guild's events stop until the reconnect: one bad
    document would interrupt everyone. The failure is contained to the event
    that caused it."""
    fake, node = stack
    seen: list = []

    async def on_track_end(gid, reason):
        raise AttributeError("'str' object has no attribute 'get'")

    async def on_track_start(gid, track):
        raise KeyError("title")

    async def on_track_exception(gid, payload):
        raise RuntimeError("boom")

    async def on_player_update(gid, state):
        seen.append(gid)

    node.on_track_end = on_track_end
    node.on_track_start = on_track_start
    node.on_track_exception = on_track_exception
    node.on_player_update = on_player_update
    node.start()
    await node.wait_ready(timeout=5)

    for kind in ("TrackEndEvent", "TrackStartEvent", "TrackExceptionEvent", "TrackStuckEvent"):
        await fake.send_event({"op": "event", "type": kind, "guildId": "123"})
    await fake.send_event({"op": "playerUpdate", "guildId": "456", "state": {}})
    await asyncio.sleep(0.1)

    assert seen == [456]                                      # still reading
    assert node.connected and len(fake.ws_connections) == 1   # no reconnect


async def test_a_raising_player_update_handler_is_contained_too(stack):
    fake, node = stack
    ended: list = []

    async def on_player_update(gid, state):
        raise ValueError("junk state")

    async def on_track_end(gid, reason):
        ended.append(gid)

    node.on_player_update = on_player_update
    node.on_track_end = on_track_end
    node.start()
    await node.wait_ready(timeout=5)

    await fake.send_event({"op": "playerUpdate", "guildId": "123", "state": {}})
    await fake.send_event({"op": "event", "type": "TrackEndEvent", "guildId": "123"})
    await asyncio.sleep(0.1)

    assert ended == [123]
    assert node.connected and len(fake.ws_connections) == 1


async def test_a_contained_handler_failure_logs_where_but_not_what(stack, caplog):
    """The exception text can carry whatever the client stored (a title, a
    URL), so the log names the exception type and the code location only."""
    fake, node = stack

    async def on_track_end(gid, reason):
        raise ValueError("invalid literal: 'SECRET-CLIENT-TEXT'")

    node.on_track_end = on_track_end
    node.start()
    await node.wait_ready(timeout=5)
    with caplog.at_level("WARNING"):
        await fake.send_event({"op": "event", "type": "TrackEndEvent", "guildId": "123"})
        await asyncio.sleep(0.1)

    text = caplog.text
    assert "ValueError" in text and "TrackEndEvent" in text
    assert "SECRET-CLIENT-TEXT" not in text
