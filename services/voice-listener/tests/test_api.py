import asyncio

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from ears.api import build_app, ship_intent
from ears.intents import Intent


class StubGateway:
    def __init__(self):
        self.calls = []
        self.vocab = {"hey", "jacky"}
    async def join(self, guild_id: str, channel_id: str, wake_phrase: str):
        self.calls.append(("join", guild_id, channel_id, wake_phrase))
    async def leave(self, guild_id: str):
        self.calls.append(("leave", guild_id))
    def knows_word(self, w: str) -> bool:
        return w in self.vocab


@pytest.fixture
async def client():
    gw = StubGateway()
    app = build_app(gw, internal_token="sekrit")
    c = TestClient(TestServer(app))
    await c.start_server()
    yield c, gw
    await c.close()


async def test_session_join(client):
    c, gw = client
    r = await c.post("/session", json={
        "guild_id": "1", "channel_id": "2",
        "wake_phrase": "hey jacky", "action": "join",
    }, headers={"X-Voice-Token": "sekrit"})
    assert r.status == 200
    assert gw.calls == [("join", "1", "2", "hey jacky")]


async def test_bad_token_rejected(client):
    c, _ = client
    r = await c.post("/session", json={}, headers={"X-Voice-Token": "wrong"})
    assert r.status == 401


async def test_validate(client):
    c, _ = client
    r = await c.post("/validate", json={"phrase": "hey zorblatt"},
                     headers={"X-Voice-Token": "sekrit"})
    body = await r.json()
    assert body == {"ok": False, "problems": ["unknown word: zorblatt"]}


async def test_session_leave(client):
    c, gw = client
    r = await c.post("/session", json={"guild_id": "1", "action": "leave"},
                     headers={"X-Voice-Token": "sekrit"})
    assert r.status == 200
    assert gw.calls == [("leave", "1")]


async def test_health_open(client):
    c, _ = client
    r = await c.get("/health")
    assert r.status == 200


async def test_ship_intent_success():
    """Bot receives the intent body + token; ship_intent reports True on 200."""
    received = {}

    async def handler(request: web.Request) -> web.Response:
        received["body"] = await request.json()
        received["token"] = request.headers.get("X-Voice-Token")
        return web.json_response({"ok": True})

    app = web.Application()
    app.add_routes([web.post("/voice-intent", handler)])
    server = TestServer(app)
    await server.start_server()
    try:
        url = str(server.make_url("/voice-intent"))
        async with aiohttp.ClientSession() as session:
            ok = await ship_intent(session, url, "sekrit", "42", Intent("skip", None))
        assert ok is True
        assert received["token"] == "sekrit"
        assert received["body"] == {"guild_id": "42", "intent": "skip", "arg": None}
    finally:
        await server.close()


async def test_ship_intent_failure_returns_false():
    """A connection error is swallowed: ship_intent returns False, never raises."""
    async with aiohttp.ClientSession() as session:
        # Nothing is listening on this port -> ClientConnectorError.
        ok = await ship_intent(session, "http://127.0.0.1:1/voice-intent",
                               "sekrit", "42", Intent("play", "a song"))
    assert ok is False


async def test_ship_intent_timeout_returns_false():
    """A hung (connected but slow) bot raises TimeoutError, NOT a ClientError;
    ship_intent must still return False rather than let it escape. Simulate the
    timeout directly (no 5s real wait) by making session.post raise it."""
    class TimingOutSession:
        def post(self, *a, **k):
            raise asyncio.TimeoutError

    ok = await ship_intent(TimingOutSession(), "http://x/voice-intent",
                           "sekrit", "42", Intent("skip", None))
    assert ok is False
