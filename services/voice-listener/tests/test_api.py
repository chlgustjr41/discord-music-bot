import pytest
from aiohttp.test_utils import TestClient, TestServer

from ears.api import build_app


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


async def test_health_open(client):
    c, _ = client
    r = await c.get("/health")
    assert r.status == 200
