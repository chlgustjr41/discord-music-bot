"""Auth routes: OAuth start/callback/poll, state lifecycle, membership gate."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from conftest import FakeRepo

from jacky.api import auth_routes as auth_mod
from jacky.api.oauth import DiscordOAuth, OAuthError
from jacky.api.tokens import TokenStore

CLIENT_ID = "cid-123"
USER_ID = "42"
USER_NAME = "jacob"


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeOAuth:
    """Programmable stand-in for DiscordOAuth."""

    client_id = CLIENT_ID

    def __init__(self) -> None:
        self.exchange_error: Exception | None = None
        self.identity_error: Exception | None = None
        self.identity = {"id": USER_ID, "username": USER_NAME}
        self.exchanged: list[str] = []

    def authorize_url(self, state: str) -> str:
        return (
            "https://discord.test/oauth2/authorize"
            f"?client_id={self.client_id}&state={state}"
        )

    async def exchange_code(self, code: str) -> str:
        self.exchanged.append(code)
        if self.exchange_error:
            raise self.exchange_error
        return "access-token"

    async def fetch_identity(self, access_token: str) -> dict:
        if self.identity_error:
            raise self.identity_error
        return dict(self.identity)


class Gate:
    """Stub member_gate: async (user_id) -> bool, programmable."""

    def __init__(self) -> None:
        self.allowed = True
        self.calls: list[str] = []

    async def __call__(self, user_id: str) -> bool:
        self.calls.append(user_id)
        return self.allowed


@pytest.fixture
def oauth():
    return FakeOAuth()


@pytest.fixture
def gate():
    return Gate()


@pytest.fixture
def repo():
    return FakeRepo()


@pytest.fixture
def token_store(repo):
    return TokenStore(repo)


@pytest.fixture
async def client(oauth, gate, token_store):
    from jacky.api.auth_routes import register_auth_routes

    app = web.Application()
    register_auth_routes(
        app, oauth=oauth, token_store=token_store, member_gate=gate
    )
    tc = TestClient(TestServer(app))
    await tc.start_server()
    yield tc
    await tc.close()


async def start(client) -> dict:
    resp = await client.post("/control/auth/start")
    assert resp.status == 200
    return await resp.json()


async def do_callback(client, state, code="c0de"):
    return await client.get(f"/control/auth/callback?code={code}&state={state}")


async def do_poll(client, state):
    return await client.get(f"/control/auth/poll?state={state}")


# ── start ────────────────────────────────────────────────────────────────


async def test_start_returns_state_and_authorize_url(client):
    body = await start(client)
    assert set(body) == {"state", "authorizeUrl"}
    assert len(body["state"]) >= 32
    assert body["state"] in body["authorizeUrl"]
    assert CLIENT_ID in body["authorizeUrl"]


async def test_start_mints_distinct_states(client):
    a = await start(client)
    b = await start(client)
    assert a["state"] != b["state"]


async def test_start_rate_limited_on_11th_call_from_one_ip(client):
    for _ in range(10):
        resp = await client.post("/control/auth/start")
        assert resp.status == 200
    resp = await client.post("/control/auth/start")
    assert resp.status == 429
    assert (await resp.json()) == {"error": "rate-limited"}


# ── callback ─────────────────────────────────────────────────────────────


async def test_callback_unknown_state_is_410_html(client):
    resp = await do_callback(client, "nope")
    assert resp.status == 410
    assert resp.content_type == "text/html"


async def test_callback_missing_code_is_410_html(client):
    body = await start(client)
    resp = await client.get(f"/control/auth/callback?state={body['state']}")
    assert resp.status == 410
    assert resp.content_type == "text/html"


async def test_callback_exchange_failure_is_502_html(client, oauth):
    oauth.exchange_error = OAuthError("token exchange failed: 400")
    body = await start(client)
    resp = await do_callback(client, body["state"])
    assert resp.status == 502
    assert resp.content_type == "text/html"


async def test_callback_identity_failure_is_502_html(client, oauth):
    oauth.identity_error = OAuthError("identity fetch failed: 401")
    body = await start(client)
    resp = await do_callback(client, body["state"])
    assert resp.status == 502
    assert resp.content_type == "text/html"


async def test_callback_success_is_200_html(client, gate):
    body = await start(client)
    resp = await do_callback(client, body["state"])
    assert resp.status == 200
    assert resp.content_type == "text/html"
    text = await resp.text()
    assert "Signed in" in text
    assert "close this tab" in text
    assert gate.calls == [USER_ID]


# ── membership gate ──────────────────────────────────────────────────────


async def test_non_member_callback_403_html_no_token_minted(client, gate, repo):
    gate.allowed = False
    body = await start(client)
    resp = await do_callback(client, body["state"])
    assert resp.status == 403
    assert resp.content_type == "text/html"
    text = await resp.text()
    assert "isn't in any server Jacky Music serves" in text
    assert repo.control_tokens == {}


async def test_non_member_poll_returns_403_json(client, gate):
    gate.allowed = False
    body = await start(client)
    await do_callback(client, body["state"])
    resp = await do_poll(client, body["state"])
    assert resp.status == 403
    assert (await resp.json()) == {"error": "not-a-member"}


# ── poll ─────────────────────────────────────────────────────────────────


async def test_poll_unknown_state_is_410_json(client):
    resp = await do_poll(client, "nope")
    assert resp.status == 410
    assert resp.content_type == "application/json"


async def test_poll_before_callback_is_202_pending(client):
    body = await start(client)
    resp = await do_poll(client, body["state"])
    assert resp.status == 202
    assert (await resp.json()) == {"status": "pending"}


async def test_poll_after_callback_returns_token_once(client, token_store):
    body = await start(client)
    await do_callback(client, body["state"])

    resp = await do_poll(client, body["state"])
    assert resp.status == 200
    claimed = await resp.json()
    assert set(claimed) == {"token", "discordUserId", "discordUserName"}
    assert claimed["discordUserId"] == USER_ID
    assert claimed["discordUserName"] == USER_NAME
    assert await token_store.resolve(claimed["token"]) == USER_ID

    # one-time claim: entry deleted on first successful poll
    second = await do_poll(client, body["state"])
    assert second.status == 410


# ── state expiry ─────────────────────────────────────────────────────────


async def test_expired_state_rejected_on_callback_and_poll(client, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(auth_mod.time, "monotonic", clock.monotonic)
    body = await start(client)

    clock.advance(auth_mod.STATE_TTL_S + 1)
    resp = await do_callback(client, body["state"])
    assert resp.status == 410
    resp = await do_poll(client, body["state"])
    assert resp.status == 410


async def test_state_within_ttl_still_valid(client, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(auth_mod.time, "monotonic", clock.monotonic)
    body = await start(client)

    clock.advance(auth_mod.STATE_TTL_S - 1)
    resp = await do_poll(client, body["state"])
    assert resp.status == 202


# ── DiscordOAuth url building (no HTTP) ──────────────────────────────────


def test_discord_oauth_authorize_url_contains_expected_params():
    oauth = DiscordOAuth(
        http=None, client_id="my-client", client_secret="s3cret",
        redirect_uri="https://control.test/control/auth/callback",
    )
    url = oauth.authorize_url("st4te")
    assert url.startswith("https://discord.com/oauth2/authorize?")
    assert "client_id=my-client" in url
    assert "state=st4te" in url
    assert "scope=identify" in url
    assert "response_type=code" in url
    assert "redirect_uri=https%3A%2F%2Fcontrol.test%2Fcontrol%2Fauth%2Fcallback" in url
    assert "s3cret" not in url
