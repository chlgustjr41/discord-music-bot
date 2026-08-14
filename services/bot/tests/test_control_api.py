"""Control API: bearer auth (TokenStore), rate limit, session resolution,
playback handlers, channel discovery."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from jacky.api.voice_actions import MAX_ACTIONS, Action
from jacky.api.voice_llm import InterpretError
from tests.conftest import FakeGuild, FakeMember, FakeVoiceState

USER_ID = 42


@pytest.fixture(autouse=True)
def _recognize_fake_member_lookup_errors(monkeypatch):
    """Teach the production except-clause about the fake's not-found error.

    FakeGuild.fetch_member raises conftest.FakeNotFound, which is not a
    discord exception, so the REST-fallback `except` would let it escape as a
    500. Every route that resolves a member by REST needs this, so it is
    autouse rather than a line each test has to remember.

    It APPENDS rather than replaces, so the real discord.NotFound /
    discord.HTTPException stay part of what's under test.

    Both module spellings are collected because `tests/` has no __init__.py:
    pytest loads the fixtures as top-level `conftest` while test modules
    import `tests.conftest`, producing two distinct class objects. Catching
    only one makes the fix depend on which module built the fake.
    """
    import sys

    from jacky.api import control

    fakes = tuple(
        mod.FakeNotFound
        for name in ("conftest", "tests.conftest")
        if (mod := sys.modules.get(name)) is not None
        and hasattr(mod, "FakeNotFound")
    )
    monkeypatch.setattr(
        control, "_MEMBER_LOOKUP_ERRORS", (*control._MEMBER_LOOKUP_ERRORS, *fakes)
    )


@pytest.fixture
async def store(service):
    from jacky.api.tokens import TokenStore

    return TokenStore(service.repo)


@pytest.fixture
async def token(store):
    # userId is stored as a STRING (Discord ids are strings in JSON and
    # Firestore); control.py converts to int at the discord.py cache edge.
    return await store.mint(str(USER_ID), "Tester")


@pytest.fixture
def auth(token):
    return {"Authorization": f"Bearer {token}"}


class FakeTranscriber:
    def __init__(self):
        self.text, self.error, self.calls = "skip", None, []
        self.languages: list[str] = []

    async def transcribe(self, wav: bytes, language: str = "en") -> str:
        self.calls.append(wav)
        self.languages.append(language)
        if self.error:
            raise self.error
        return self.text


@pytest.fixture
def transcriber():
    return FakeTranscriber()


class FakeInterpreter:
    def __init__(self):
        self.actions, self.error, self.calls = None, None, []

    async def interpret(self, transcript, keywords=()):
        # (transcript, keywords) rather than the transcript alone: the hints
        # are part of the contract the route has to honour, and `calls == []`
        # is how the grammar-first win is asserted.
        self.calls.append((transcript, list(keywords)))
        if self.error:
            raise self.error
        return self.actions if self.actions is not None else [Action("skip")]


class RecordingDispatcher:
    """Captures exactly what reached dispatch — the only place the ENFORCED
    action is visible, since the response reports the verb but not placement."""

    def __init__(self):
        self.dispatched: list[list[Action]] = []

    async def dispatch_all(self, guild_id, actions):
        from jacky.voice_control import DispatchResult

        self.dispatched.append(list(actions))
        return [DispatchResult(ok=True, detail="ok") for _ in actions]


@pytest.fixture
def interpreter():
    return FakeInterpreter()


# Distinguishes "caller didn't care, build the real thing" from an explicit
# None, which is the production-with-no-API-key configuration (503).
_BUILD_DEFAULT = object()


def build_client(
    service, store, limiter=None,
    transcriber=_BUILD_DEFAULT, voice_dispatcher=_BUILD_DEFAULT,
    interpreter=_BUILD_DEFAULT,
) -> TestClient:
    from jacky.announce import Announcer
    from jacky.api.control import register_control_routes
    from jacky.api.ratelimit import SlidingWindow
    from jacky.core.health import build_app
    from jacky.voice_control import VoiceIntentDispatcher

    app = build_app(service.bot, service)
    # ONE announcer for the route AND the dispatcher, mirroring core/bot.py:
    # the shared 10 s window across voice and key posts is wiring, and the
    # route-level cross-window test exercises exactly this.
    announcer = Announcer(service, service.bot)
    register_control_routes(
        app, bot=service.bot, service=service, token_store=store,
        limiter=limiter or SlidingWindow(limit=1000, window_s=60),
        transcriber=(
            FakeTranscriber() if transcriber is _BUILD_DEFAULT else transcriber
        ),
        voice_dispatcher=(
            VoiceIntentDispatcher(service, service.repo, announcer)
            if voice_dispatcher is _BUILD_DEFAULT
            else voice_dispatcher
        ),
        interpreter=(
            FakeInterpreter() if interpreter is _BUILD_DEFAULT else interpreter
        ),
        announcer=announcer,
    )
    return TestClient(TestServer(app))


@pytest.fixture
async def client(service, store, transcriber, interpreter):
    tc = build_client(
        service, store, transcriber=transcriber, interpreter=interpreter
    )
    await tc.start_server()
    yield tc
    await tc.close()


def put_user_in_voice(service, guild_id, user_id=USER_ID, channel_id=99):
    """Place a fake human in a voice channel of the (already-active) guild."""
    guild = service.bot.get_guild(guild_id)
    channel = guild.add_voice_channel(channel_id)
    guild.members_by_id[user_id] = FakeMember(
        id=user_id, voice=FakeVoiceState(channel=channel)
    )


# ── auth ─────────────────────────────────────────────────────────────────

async def test_missing_auth_header_is_401(client):
    resp = await client.get("/control/now-playing")
    assert resp.status == 401
    assert (await resp.json()) == {"error": "unauthorized"}


async def test_malformed_auth_header_is_401(client, token):
    # Right token, wrong scheme: no "Bearer " prefix.
    resp = await client.get(
        "/control/now-playing", headers={"Authorization": f"Token {token}"}
    )
    assert resp.status == 401


async def test_unknown_token_is_401(client):
    resp = await client.get(
        "/control/now-playing", headers={"Authorization": f"Bearer {'0' * 64}"}
    )
    assert resp.status == 401


async def test_revoked_token_is_401(client, store, auth):
    resp = await client.get("/control/now-playing", headers=auth)
    assert resp.status == 200
    await store.revoke_user(str(USER_ID))
    resp = await client.get("/control/now-playing", headers=auth)
    assert resp.status == 401


async def test_health_stays_unauthenticated(client):
    resp = await client.get("/health")
    assert resp.status == 200


async def test_rate_limit_429_after_window_exhausted(service, store, auth):
    from jacky.api.ratelimit import SlidingWindow

    tc = build_client(service, store, limiter=SlidingWindow(limit=3, window_s=60))
    await tc.start_server()
    try:
        for _ in range(3):
            resp = await tc.get("/control/now-playing", headers=auth)
            assert resp.status == 200
        resp = await tc.get("/control/now-playing", headers=auth)
        assert resp.status == 429
        assert (await resp.json()) == {"error": "rate-limited"}
    finally:
        await tc.close()


async def test_all_control_routes_require_auth(client):
    """Sweep every registered /control/* route: no auth -> 401. Guards against
    a future route being added without the guarded() wrapper. Only control
    routes are registered in this fixture (auth routes have their own suite),
    so the sweep sees exactly the guarded set."""
    seen_paths = set()
    for resource in client.server.app.router.resources():
        info = resource.get_info()
        path = info.get("path") or info.get("formatter") or ""
        if not path.startswith("/control/"):
            continue
        for route in resource:  # includes aiohttp's implicit HEAD for GETs
            resp = await client.request(route.method, path)
            assert resp.status == 401, f"{route.method} {path}"
            seen_paths.add(path)
    # now-playing + 5 actions + channels + summon
    # + playlists + playlist + dashboard-url + voice + announce
    assert len(seen_paths) == 13


async def test_string_user_id_resolves_int_keyed_member(client, service, guild_id, auth):
    """TokenStore hands back the userId as a string; the member cache is
    keyed by int. A live session must still resolve."""
    put_user_in_voice(service, guild_id)  # member stored under int key
    resp = await client.get("/control/now-playing", headers=auth)
    assert (await resp.json())["active"] is True


# ── session resolution / now-playing ─────────────────────────────────────

async def test_now_playing_inactive_when_user_not_in_voice(client, auth):
    resp = await client.get("/control/now-playing", headers=auth)
    assert resp.status == 200
    assert await resp.json() == {"active": False}


async def test_now_playing_inactive_when_bot_has_no_voice_client(
    client, service, guild_id, auth
):
    service.bot.get_guild(guild_id).voice_client = None
    put_user_in_voice(service, guild_id)
    resp = await client.get("/control/now-playing", headers=auth)
    assert (await resp.json()) == {"active": False}


async def test_now_playing_reports_current_track(client, service, guild_id, sid, auth):
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {
        "currentTrack": {"title": "Song", "artist": "Artist"},
        "isPaused": False, "volume": 70,
    })
    resp = await client.get("/control/now-playing", headers=auth)
    body = await resp.json()
    assert body == {
        "active": True, "title": "Song", "author": "Artist",
        "paused": False, "volume": 70, "guildName": "Guild",
        "thumbnail": None,
    }


async def test_now_playing_active_but_idle(client, service, guild_id, auth):
    put_user_in_voice(service, guild_id)
    resp = await client.get("/control/now-playing", headers=auth)
    body = await resp.json()
    assert body["active"] is True and body["title"] is None


async def test_resolution_picks_guild_where_user_sits_in_voice(
    client, service, guild_id, auth
):
    """Two guilds with live sessions; the user is only in voice in the second."""
    from tests.conftest import FakeVoice

    other = FakeGuild(id=777, voice_client=FakeVoice(), name="Other")
    service.bot.guilds.insert(0, other)  # scanned first, must NOT match
    await service.repo.init_state("777")
    put_user_in_voice(service, guild_id)
    resp = await client.get("/control/now-playing", headers=auth)
    body = await resp.json()
    assert body["guildName"] == "Guild"


async def test_member_in_voice_state_without_channel_is_inactive(
    client, service, guild_id, auth
):
    """Transient discord.py state: member has a VoiceState but channel=None."""
    guild = service.bot.get_guild(guild_id)
    guild.members_by_id[USER_ID] = FakeMember(
        id=USER_ID, voice=FakeVoiceState(channel=None)
    )
    resp = await client.get("/control/now-playing", headers=auth)
    assert (await resp.json()) == {"active": False}


async def test_now_playing_survives_null_volume(client, service, guild_id, sid, auth):
    """Web app can write volume: null; must not 500."""
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {"volume": None})
    resp = await client.get("/control/now-playing", headers=auth)
    assert resp.status == 200
    assert (await resp.json())["volume"] == 80


async def test_volume_zero_is_not_treated_as_unset(client, service, guild_id, sid, auth):
    """j!volume 0 (mute) must report 0 and delta from 0, not from 80."""
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {"volume": 0})
    resp = await client.get("/control/now-playing", headers=auth)
    assert (await resp.json())["volume"] == 0
    resp = await client.post("/control/volume", json={"delta": 5}, headers=auth)
    assert (await resp.json()) == {"volume": 5}


# ── actions ──────────────────────────────────────────────────────────────

async def test_actions_409_without_session(client, auth):
    for path in ("/control/play-pause", "/control/skip",
                 "/control/stop", "/control/volume", "/control/shuffle"):
        resp = await client.post(path, json={"delta": 5}, headers=auth)
        assert resp.status == 409, path


async def test_play_pause_toggles(client, service, guild_id, sid, auth):
    put_user_in_voice(service, guild_id)
    resp = await client.post("/control/play-pause", headers=auth)
    assert resp.status == 200 and (await resp.json()) == {"paused": True}
    assert service.node.updates[-1] == (guild_id, {"paused": True})
    assert (await service.repo.get_state(sid))["isPaused"] is True

    resp = await client.post("/control/play-pause", headers=auth)
    assert (await resp.json()) == {"paused": False}
    assert service.node.updates[-1] == (guild_id, {"paused": False})


async def test_skip_stops_current_track(client, service, guild_id, auth):
    put_user_in_voice(service, guild_id)
    resp = await client.post("/control/skip", headers=auth)
    assert resp.status == 200
    assert service.node.updates[-1] == (guild_id, {"track": {"encoded": None}})


async def test_stop_tears_down_session(client, service, guild_id, sid, auth):
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {"queue": [{"title": "x"}]})
    resp = await client.post("/control/stop", headers=auth)
    assert resp.status == 200
    state = await service.repo.get_state(sid)
    assert state["isPlaying"] is False and state["queue"] == []
    assert service.bot.get_guild(guild_id).voice_client is None or \
        service.bot.get_guild(guild_id).voice_client.disconnected


async def test_volume_applies_delta_and_clamps(client, service, guild_id, sid, auth):
    put_user_in_voice(service, guild_id)
    resp = await client.post("/control/volume", json={"delta": 5}, headers=auth)
    assert (await resp.json()) == {"volume": 85}  # init_state volume=80

    await service.repo.update_state(sid, {"volume": 98})
    resp = await client.post("/control/volume", json={"delta": 5}, headers=auth)
    assert (await resp.json()) == {"volume": 100}


async def test_volume_missing_delta_is_400(client, service, guild_id, auth):
    put_user_in_voice(service, guild_id)
    resp = await client.post("/control/volume", json={}, headers=auth)
    assert resp.status == 400


def spy_on_shuffle(service) -> list:
    """Record the sids handed to repo.shuffle_queue, delegating to the real one.

    FakeRepo.shuffle_queue only REPORTS len(queue) — it never reorders and
    never writes — so "the queue came back in a different order" is not an
    observable here. The delegation itself is what this route owes: the
    voice dispatcher and j!shuffle call the same repository method, and a
    route that shuffled on its own would be the divergence the spec forbids.
    """
    calls: list = []
    original = service.repo.shuffle_queue

    async def spy(sid):
        calls.append(sid)
        return await original(sid)

    service.repo.shuffle_queue = spy
    return calls


async def test_shuffle_shuffles_the_queue_and_returns_the_count(
    client, service, guild_id, sid, auth
):
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(
        sid, {"queue": [{"title": t} for t in ("a", "b", "c", "d")]}
    )
    calls = spy_on_shuffle(service)

    resp = await client.post("/control/shuffle", headers=auth)
    assert resp.status == 200
    assert (await resp.json()) == {"ok": True, "count": 4}
    assert calls == [sid]


async def test_shuffle_of_an_empty_queue_succeeds_with_zero(
    client, service, guild_id, sid, auth
):
    """Shuffling nothing is not an error — the key must not flash alert."""
    put_user_in_voice(service, guild_id)
    assert await service.repo.get_queue(sid) == []
    calls = spy_on_shuffle(service)

    resp = await client.post("/control/shuffle", headers=auth)
    assert resp.status == 200
    assert (await resp.json()) == {"ok": True, "count": 0}
    assert calls == [sid]


async def test_shuffle_logs_one_history_row(client, service, guild_id, sid, auth):
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {"queue": [{"title": "a"}]})
    before = len(service.repo.command_log)

    resp = await client.post("/control/shuffle", headers=auth)
    assert resp.status == 200
    assert len(service.repo.command_log) - before == 1
    entry = service.repo.command_log[-1]
    # (sid, command, args, user, source, transcript). "shuffle" is the j!
    # command name, so the dashboard's history renders the row exactly as it
    # renders a typed j!shuffle.
    assert entry[0] == sid
    assert entry[1] == "shuffle"
    assert entry[3] == "Stream Deck"
    assert entry[4] == "streamdeck"


# ── channel discovery ────────────────────────────────────────────────────

async def test_channels_lists_activated_guilds_where_member(
    client, service, guild_id, auth
):
    guild = service.bot.get_guild(guild_id)
    guild.add_voice_channel(99, name="General")
    guild.add_voice_channel(100, name="Music")
    guild.members_by_id[USER_ID] = FakeMember(id=USER_ID)
    resp = await client.get("/control/channels", headers=auth)
    assert resp.status == 200
    assert (await resp.json()) == [{
        "guildId": str(guild_id), "guildName": "Guild",
        "channels": [
            {"id": "99", "name": "General"},
            {"id": "100", "name": "Music"},
        ],
    }]


async def test_channels_excludes_not_activated_guild(client, service, guild_id, auth):
    other = FakeGuild(id=777, name="Other")
    other.add_voice_channel(50, name="Lounge")
    other.members_by_id[USER_ID] = FakeMember(id=USER_ID)
    service.bot.guilds.append(other)
    service.repo.activated_overrides["777"] = False

    guild = service.bot.get_guild(guild_id)
    guild.add_voice_channel(99, name="General")
    guild.members_by_id[USER_ID] = FakeMember(id=USER_ID)

    resp = await client.get("/control/channels", headers=auth)
    body = await resp.json()
    assert [g["guildId"] for g in body] == [str(guild_id)]


async def test_channels_excludes_guild_where_not_member(client, service, guild_id, auth):
    other = FakeGuild(id=777, name="Other")
    other.add_voice_channel(50, name="Lounge")  # activated, but user absent
    service.bot.guilds.append(other)

    guild = service.bot.get_guild(guild_id)
    guild.add_voice_channel(99, name="General")
    guild.members_by_id[USER_ID] = FakeMember(id=USER_ID)

    resp = await client.get("/control/channels", headers=auth)
    body = await resp.json()
    assert [g["guildId"] for g in body] == [str(guild_id)]


async def test_channels_empty_when_no_qualifying_guilds(client, service, guild_id, auth):
    """Member of the guild but it's deactivated -> 200 with an empty list,
    not an error (the PI renders 'no servers')."""
    guild = service.bot.get_guild(guild_id)
    guild.add_voice_channel(99, name="General")
    guild.members_by_id[USER_ID] = FakeMember(id=USER_ID)
    service.repo.activated_overrides[str(guild_id)] = False

    resp = await client.get("/control/channels", headers=auth)
    assert resp.status == 200
    assert (await resp.json()) == []


# ── summon ───────────────────────────────────────────────────────────────

def summon_body(guild_id, channel_id):
    return {"guildId": str(guild_id), "channelId": str(channel_id)}


async def test_summon_joins_and_opens_session(client, service, guild_id, sid, auth):
    guild = service.bot.get_guild(guild_id)
    guild.voice_client = None  # not connected anywhere
    guild.add_voice_channel(99, name="Music")
    guild.members_by_id[USER_ID] = FakeMember(id=USER_ID)

    resp = await client.post(
        "/control/summon", json=summon_body(guild_id, 99), headers=auth
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["action"] == "joined" and body["sessionCode"]
    assert guild.voice_client is not None  # channel.connect ran
    state = await service.repo.get_state(sid)
    assert state["voiceChannelId"] == "99"
    assert state["sessionCode"] == body["sessionCode"]


async def test_summon_same_channel_leaves_and_requeues_current(
    client, service, guild_id, sid, auth
):
    from tests.conftest import FakeVoice

    guild = service.bot.get_guild(guild_id)
    channel = guild.add_voice_channel(99, name="Music")
    guild.voice_client = FakeVoice(channel=channel)
    guild.members_by_id[USER_ID] = FakeMember(id=USER_ID)
    await service.repo.update_state(sid, {
        "currentTrack": {"title": "Now", "startedAt": 123},
        "queue": [{"title": "Next"}],
    })

    resp = await client.post(
        "/control/summon", json=summon_body(guild_id, 99), headers=auth
    )
    assert resp.status == 200
    assert (await resp.json()) == {"action": "left"}
    state = await service.repo.get_state(sid)
    # Current track requeued at the head, existing queue preserved.
    assert [t["title"] for t in state["queue"]] == ["Now", "Next"]
    assert "startedAt" not in state["queue"][0]
    assert state["currentTrack"] is None
    assert guild.voice_client.disconnected


async def test_summon_409_when_active_in_another_channel(
    client, service, guild_id, auth
):
    from tests.conftest import FakeVoice

    guild = service.bot.get_guild(guild_id)
    elsewhere = guild.add_voice_channel(50, name="Other")
    guild.add_voice_channel(99, name="Music")
    guild.voice_client = FakeVoice(channel=elsewhere)
    guild.members_by_id[USER_ID] = FakeMember(id=USER_ID)

    resp = await client.post(
        "/control/summon", json=summon_body(guild_id, 99), headers=auth
    )
    assert resp.status == 409
    assert (await resp.json()) == {"error": "active-elsewhere"}


async def test_summon_403_for_non_member(client, service, guild_id, auth, monkeypatch):
    """Absent from the member cache AND fetch_member raises -> 403."""
    # Import conftest by the SAME module name pytest loaded it under
    # (top-level "conftest", no tests/__init__.py): exception identity must
    # match the raise site in FakeGuild.fetch_member, and importing it as
    # tests.conftest would yield a second, distinct FakeNotFound class.
    import conftest
    from jacky.api import control

    monkeypatch.setattr(
        control, "_MEMBER_LOOKUP_ERRORS", (conftest.FakeNotFound,)
    )
    guild = service.bot.get_guild(guild_id)
    guild.voice_client = None
    guild.add_voice_channel(99)  # guild exists; user just isn't in it

    resp = await client.post(
        "/control/summon", json=summon_body(guild_id, 99), headers=auth
    )
    assert resp.status == 403
    assert (await resp.json()) == {"error": "not-a-member"}


async def test_summon_unknown_guild_is_403_not_a_member(client, auth):
    """Unknown guild id gets the same error as non-membership so the
    endpoint doesn't leak which guilds exist."""
    resp = await client.post(
        "/control/summon", json=summon_body(999999, 99), headers=auth
    )
    assert resp.status == 403
    assert (await resp.json()) == {"error": "not-a-member"}


async def test_summon_403_when_guild_not_activated(client, service, guild_id, auth):
    service.repo.activated_overrides[str(guild_id)] = False
    guild = service.bot.get_guild(guild_id)
    guild.voice_client = None
    guild.add_voice_channel(99)
    guild.members_by_id[USER_ID] = FakeMember(id=USER_ID)

    resp = await client.post(
        "/control/summon", json=summon_body(guild_id, 99), headers=auth
    )
    assert resp.status == 403
    assert (await resp.json()) == {"error": "not-activated"}


async def test_summon_400_for_unknown_or_unconnectable_channel(
    client, service, guild_id, auth
):
    from types import SimpleNamespace

    guild = service.bot.get_guild(guild_id)
    guild.voice_client = None
    guild.members_by_id[USER_ID] = FakeMember(id=USER_ID)
    # A text-like channel: present in the cache but has no connect().
    guild.channels[77] = SimpleNamespace(id=77, name="general-text")

    for channel_id in (12345, 77):  # nonexistent / not connectable
        resp = await client.post(
            "/control/summon", json=summon_body(guild_id, channel_id), headers=auth
        )
        assert resp.status == 400, channel_id
        assert (await resp.json()) == {"error": "bad-channel"}


async def test_summon_400_for_missing_or_non_numeric_fields(client, auth):
    bad_bodies = [
        {},
        {"guildId": "123"},
        {"channelId": "99"},
        {"guildId": "abc", "channelId": "99"},
        {"guildId": "123", "channelId": "abc"},
    ]
    for body in bad_bodies:
        resp = await client.post("/control/summon", json=body, headers=auth)
        assert resp.status == 400, body
        assert (await resp.json()) == {"error": "bad-request"}


async def test_summon_502_when_connect_fails(client, service, guild_id, auth):
    guild = service.bot.get_guild(guild_id)
    guild.voice_client = None
    channel = guild.add_voice_channel(99)
    guild.members_by_id[USER_ID] = FakeMember(id=USER_ID)

    async def boom(*, cls=None):
        raise RuntimeError("missing voice permission (fake)")

    channel.connect = boom  # instance attr shadows the dataclass method

    resp = await client.post(
        "/control/summon", json=summon_body(guild_id, 99), headers=auth
    )
    assert resp.status == 502
    assert (await resp.json()) == {"error": "join-failed"}


# ── deactivation cuts off control (security audit H1) ────────────────────

async def test_deactivated_guild_has_no_controllable_session(
    client, service, guild_id, sid, auth
):
    """Turning a server off must cut EVERY control path, not just j! commands.

    The token was minted while the server was active and nothing re-checks it
    on later requests, so without an activation check in resolve_guild a live
    session stays remotely controllable after the owner switches the bot off.
    """
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {"currentTrack": {"title": "Song"}})
    service.repo.activated_overrides[str(guild_id)] = False

    resp = await client.get("/control/now-playing", headers=auth)
    assert (await resp.json()) == {"active": False}

    for path in ("/control/play-pause", "/control/skip",
                 "/control/stop", "/control/volume", "/control/shuffle"):
        resp = await client.post(path, json={"delta": 5}, headers=auth)
        assert resp.status == 409, path


# ── now-playing artwork ──────────────────────────────────────────────────

async def test_now_playing_includes_thumbnail(client, service, guild_id, sid, auth):
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {
        "currentTrack": {"title": "Song", "artist": "A", "thumbnail": "https://i/t.jpg"},
    })
    body = await (await client.get("/control/now-playing", headers=auth)).json()
    assert body["thumbnail"] == "https://i/t.jpg"


async def test_now_playing_thumbnail_is_null_when_absent(
    client, service, guild_id, sid, auth
):
    """Idle session and a track with no artwork both report null, so the key
    clears its image instead of keeping the previous track's cover."""
    put_user_in_voice(service, guild_id)
    body = await (await client.get("/control/now-playing", headers=auth)).json()
    assert body["thumbnail"] is None

    await service.repo.update_state(sid, {"currentTrack": {"title": "Song"}})
    body = await (await client.get("/control/now-playing", headers=auth)).json()
    assert body["thumbnail"] is None


# ── playlists listing ────────────────────────────────────────────────────

async def test_playlists_lists_activated_guilds_with_counts(
    client, service, guild_id, sid, auth
):
    guild = service.bot.get_guild(guild_id)
    guild.members_by_id[USER_ID] = FakeMember(id=USER_ID)
    await service.repo.save_playlist(sid, "Chill", [{"title": "a"}, {"title": "b"}], "me")
    await service.repo.save_playlist(sid, "Hype", [{"title": "c"}], "me")

    body = await (await client.get("/control/playlists", headers=auth)).json()
    assert body == [{
        "guildId": sid,
        "guildName": "Guild",
        "playlists": [
            {"name": "Chill", "trackCount": 2},
            {"name": "Hype", "trackCount": 1},
        ],
    }]


async def test_playlists_excludes_deactivated_and_non_member_guilds(
    client, service, guild_id, sid, auth
):
    guild = service.bot.get_guild(guild_id)
    guild.members_by_id[USER_ID] = FakeMember(id=USER_ID)
    await service.repo.save_playlist(sid, "Chill", [{"title": "a"}], "me")
    service.repo.activated_overrides[sid] = False
    assert (await (await client.get("/control/playlists", headers=auth)).json()) == []

    service.repo.activated_overrides[sid] = True
    del guild.members_by_id[USER_ID]
    assert (await (await client.get("/control/playlists", headers=auth)).json()) == []


async def test_playlists_returns_empty_list_when_none_saved(
    client, service, guild_id, auth
):
    service.bot.get_guild(guild_id).members_by_id[USER_ID] = FakeMember(id=USER_ID)
    body = await (await client.get("/control/playlists", headers=auth)).json()
    assert body == [{"guildId": str(guild_id), "guildName": "Guild", "playlists": []}]


# ── playlist insert-and-play ─────────────────────────────────────────────

async def arm_playlist_guild(service, guild_id, name="Chill", tracks=None):
    """Member present, in voice, with a live session — the state a playlist
    press requires."""
    put_user_in_voice(service, guild_id)
    await service.repo.save_playlist(
        str(guild_id),
        name,
        [{"title": "P1"}, {"title": "P2"}] if tracks is None else tracks,
        "me",
    )


async def test_playlist_inserts_at_front_and_skips_when_playing(
    client, service, guild_id, sid, auth
):
    await arm_playlist_guild(service, guild_id)
    await service.repo.update_state(sid, {
        "queue": [{"title": "Old"}], "currentTrack": {"title": "Now"},
    })

    resp = await client.post(
        "/control/playlist",
        json={"guildId": sid, "playlistName": "Chill"},
        headers=auth,
    )
    assert resp.status == 200
    assert (await resp.json()) == {"inserted": 2, "playlistName": "Chill"}

    queue = (await service.repo.get_state(sid))["queue"]
    assert [t["title"] for t in queue] == ["P1", "P2", "Old"]
    assert queue[0]["requestedBy"] == "Tester"
    # Something was playing, so it advances via the proven TrackEnd path.
    assert service.node.updates[-1] == (guild_id, {"track": {"encoded": None}})


async def test_playlist_starts_playback_when_idle(client, service, guild_id, sid, auth):
    """Nothing playing: a skip would be a no-op, so play_next runs instead."""
    from jacky.audio.models import LoadResult, to_identifier
    from tests.conftest import make_track

    await arm_playlist_guild(service, guild_id)
    await service.repo.update_state(sid, {"queue": [], "currentTrack": None})
    # FakeNode resolves every search to one canned track, and play_next merges
    # the RESOLVED title over the queued one. Without a P1-specific result the
    # assertion below would read the canned title and say nothing about which
    # entry was popped.
    service.node.load_results[to_identifier("P1")] = LoadResult(
        kind="track", tracks=[make_track(title="P1")]
    )

    resp = await client.post(
        "/control/playlist",
        json={"guildId": sid, "playlistName": "Chill"},
        headers=auth,
    )
    assert resp.status == 200
    state = await service.repo.get_state(sid)
    assert state["currentTrack"]["title"] == "P1"
    assert [t["title"] for t in state["queue"]] == ["P2"]


async def test_playlist_unknown_name_is_404(client, service, guild_id, sid, auth):
    await arm_playlist_guild(service, guild_id)
    resp = await client.post(
        "/control/playlist",
        json={"guildId": sid, "playlistName": "Nope"},
        headers=auth,
    )
    assert resp.status == 404
    assert (await resp.json())["error"] == "no-such-playlist"


async def test_playlist_empty_playlist_is_404(client, service, guild_id, sid, auth):
    await arm_playlist_guild(service, guild_id, name="Empty", tracks=[])
    resp = await client.post(
        "/control/playlist",
        json={"guildId": sid, "playlistName": "Empty"},
        headers=auth,
    )
    assert resp.status == 404


async def test_playlist_requires_a_live_session(client, service, guild_id, sid, auth):
    """Member of the guild, playlist exists, but the bot isn't connected."""
    service.bot.get_guild(guild_id).members_by_id[USER_ID] = FakeMember(id=USER_ID)
    service.bot.get_guild(guild_id).voice_client = None
    await service.repo.save_playlist(sid, "Chill", [{"title": "P1"}], "me")
    resp = await client.post(
        "/control/playlist",
        json={"guildId": sid, "playlistName": "Chill"},
        headers=auth,
    )
    assert resp.status == 409
    assert (await resp.json())["error"] == "no-active-session"


async def test_playlist_rejects_bad_body_and_outsiders(client, service, guild_id, auth):
    resp = await client.post("/control/playlist", json={}, headers=auth)
    assert resp.status == 400

    resp = await client.post(
        "/control/playlist",
        json={"guildId": "999999", "playlistName": "Chill"},
        headers=auth,
    )
    assert resp.status == 403


async def test_playlist_rejects_invalid_document_ids(client, service, guild_id, sid, auth):
    """Firestore document-id rules: "/" is a path separator, "."/".." are
    traversal, __x__ is reserved. Each would reach the SDK as a 500 rather
    than a clean 400 if we let it through."""
    await arm_playlist_guild(service, guild_id)
    for bad in ("a/b", ".", "..", "__proto__"):
        resp = await client.post(
            "/control/playlist",
            json={"guildId": sid, "playlistName": bad},
            headers=auth,
        )
        assert resp.status == 400, bad


async def test_playlist_decides_before_writing_the_queue(
    client, service, guild_id, sid, auth
):
    """The queue write wakes the Firestore listener, which auto-starts playback
    when it sees the queue grow while idle. Reading state after the write would
    leave a window for it to pop the track we just inserted."""
    await arm_playlist_guild(service, guild_id)
    await service.repo.update_state(sid, {"queue": [], "currentTrack": None})

    order: list[str] = []
    real_update, real_get = service.repo.update_state, service.repo.get_state

    async def spy_update(s, data):
        if "queue" in data:
            order.append("write")
        return await real_update(s, data)

    async def spy_get(s):
        order.append("read")
        return await real_get(s)

    service.repo.update_state, service.repo.get_state = spy_update, spy_get
    try:
        resp = await client.post(
            "/control/playlist",
            json={"guildId": sid, "playlistName": "Chill"},
            headers=auth,
        )
    finally:
        service.repo.update_state, service.repo.get_state = real_update, real_get
    assert resp.status == 200
    assert order.index("read") < order.index("write")


# ── dashboard url ────────────────────────────────────────────────────────

async def test_dashboard_url_points_at_the_live_session(
    client, service, guild_id, sid, auth
):
    put_user_in_voice(service, guild_id)
    await service.repo.set_session_code(sid, "ABC123")
    body = await (await client.get("/control/dashboard-url", headers=auth)).json()
    assert body == {
        "active": True,
        "url": "http://web.test/dashboard/ABC123",
        "guildName": "Guild",
    }


async def test_dashboard_url_falls_back_to_the_entry_page(client, auth):
    """No session: the key still opens something useful rather than failing."""
    body = await (await client.get("/control/dashboard-url", headers=auth)).json()
    assert body == {"active": False, "url": "http://web.test/app"}


async def test_dashboard_url_falls_back_when_session_code_missing(
    client, service, guild_id, sid, auth
):
    """Live session whose code was invalidated mid-teardown — never build
    '/dashboard/None'."""
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {"sessionCode": None})
    body = await (await client.get("/control/dashboard-url", headers=auth)).json()
    assert body["active"] is False
    assert body["url"] == "http://web.test/app"


# ── PI dropdowns must work when you are NOT in voice ─────────────────────

async def test_channels_lists_guilds_when_the_member_cache_misses(
    client, service, guild_id, auth
):
    """Configuring a key happens while you're sitting at your desk, not in a
    voice channel — and the bot runs without the privileged members intent, so
    discord.py's cache holds roughly the bot plus whoever is in voice. A
    cache-only membership check therefore hides every guild and the dropdown
    comes back empty, which is exactly what "can't customize the key" looks
    like. The listing endpoints must fall back to REST like summon does.
    """
    guild = service.bot.get_guild(guild_id)
    guild.add_voice_channel(99, "General")
    guild.members_by_id.clear()                       # not in voice -> cache miss
    guild.rest_members_by_id[USER_ID] = FakeMember(id=USER_ID)

    body = await (await client.get("/control/channels", headers=auth)).json()
    assert [g["guildId"] for g in body] == [str(guild_id)]
    assert body[0]["channels"] == [{"id": "99", "name": "General"}]


async def test_playlists_lists_guilds_when_the_member_cache_misses(
    client, service, guild_id, sid, auth
):
    """Same cache-miss gap as channels — see that test for why."""
    guild = service.bot.get_guild(guild_id)
    guild.members_by_id.clear()
    guild.rest_members_by_id[USER_ID] = FakeMember(id=USER_ID)
    await service.repo.save_playlist(sid, "Chill", [{"title": "a"}], "me")

    body = await (await client.get("/control/playlists", headers=auth)).json()
    assert [g["guildId"] for g in body] == [str(guild_id)]
    assert body[0]["playlists"] == [{"name": "Chill", "trackCount": 1}]


async def test_listings_still_exclude_true_outsiders(
    client, service, guild_id, sid, auth, monkeypatch
):
    """The REST fallback must not turn into 'everyone sees every guild'."""
    import conftest

    from jacky.api import control

    monkeypatch.setattr(
        control, "_MEMBER_LOOKUP_ERRORS", (conftest.FakeNotFound,)
    )
    guild = service.bot.get_guild(guild_id)
    guild.members_by_id.clear()
    guild.rest_members_by_id.clear()                  # genuinely not a member
    await service.repo.save_playlist(sid, "Chill", [{"title": "a"}], "me")

    assert (await (await client.get("/control/channels", headers=auth)).json()) == []
    assert (await (await client.get("/control/playlists", headers=auth)).json()) == []


# ── voice command route ──────────────────────────────────────────────────

WAV = b"RIFF" + b"\x00" * 200

# Speech the deterministic grammar declines, so the reasoning layer is what
# answers. Tests about the INTERPRETER's output must use it: a transcript the
# grammar resolves never reaches the interpreter at all, which would make them
# assert against the fake's actions while the grammar's were what ran.
UNINTERPRETABLE = "hey can you do a thing"


async def test_voice_requires_a_live_session_before_transcribing(
    client, service, auth, transcriber
):
    """409 must come BEFORE transcription — never pay for a doomed request."""
    resp = await client.post("/control/voice", data=WAV, headers=auth)
    assert resp.status == 409
    assert transcriber.calls == []


async def test_voice_runs_the_recognized_command(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    put_user_in_voice(service, guild_id)
    transcriber.text = "skip"
    interpreter.actions = [Action("skip")]
    resp = await client.post("/control/voice", data=WAV, headers=auth)
    body = await resp.json()
    assert resp.status == 200
    assert body["transcript"] == "skip"
    assert [a["action"] for a in body["actions"]] == ["skip"]
    assert body["ok"] is True
    assert service.node.updates[-1] == (guild_id, {"track": {"encoded": None}})


async def test_voice_logs_to_command_history_with_transcript(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    put_user_in_voice(service, guild_id)
    transcriber.text = "play a song"
    interpreter.actions = [Action("play", query="a song", placement="now")]
    await client.post("/control/voice", data=WAV, headers=auth)
    entry = service.repo.command_log[-1]
    assert entry[1] == "play"            # executed action, so retrigger works
    assert entry[2] == "a song"
    assert entry[4] == "voice"
    assert entry[5] == "play a song"     # the recognized speech


async def test_voice_rejects_oversized_bodies(client, service, guild_id, auth):
    put_user_in_voice(service, guild_id)
    resp = await client.post("/control/voice", data=b"\x00" * 700_000, headers=auth)
    assert resp.status == 413


async def test_voice_empty_transcript_is_422(
    client, service, guild_id, auth, transcriber, interpreter
):
    put_user_in_voice(service, guild_id)
    transcriber.text = "   "
    interpreter.actions = []   # nothing said, so nothing to interpret
    resp = await client.post("/control/voice", data=WAV, headers=auth)
    assert resp.status == 422
    assert (await resp.json())["error"] == "no-speech"


async def test_voice_transcription_failure_is_502(
    client, service, guild_id, auth, transcriber
):
    from jacky.api.transcribe import TranscribeError

    put_user_in_voice(service, guild_id)
    transcriber.error = TranscribeError("boom")
    resp = await client.post("/control/voice", data=WAV, headers=auth)
    assert resp.status == 502
    assert (await resp.json())["error"] == "stt-failed"


async def test_voice_dispatch_failure_is_502_not_500(
    service, store, guild_id, auth, transcriber
):
    """The dispatcher touches Firestore on the volume and playlist paths. A
    transient failure there must surface as a handled 502, not an unhandled
    500 the spec's error table has no entry for."""
    class BoomDispatcher:
        async def dispatch_all(self, guild_id, actions):
            raise RuntimeError("firestore is having a day")

    tc = build_client(
        service, store, transcriber=transcriber, voice_dispatcher=BoomDispatcher()
    )
    await tc.start_server()
    try:
        put_user_in_voice(service, guild_id)
        resp = await tc.post("/control/voice", data=WAV, headers=auth)
        assert resp.status == 502
        assert (await resp.json())["error"] == "dispatch-failed"
    finally:
        await tc.close()


async def test_voice_is_503_when_transcription_is_not_configured(
    service, store, guild_id, auth
):
    """Production with no OPENAI_API_KEY: the route is still registered (so the
    auth sweep covers it) but reports itself unavailable."""
    tc = build_client(service, store, transcriber=None, voice_dispatcher=None)
    await tc.start_server()
    try:
        put_user_in_voice(service, guild_id)
        resp = await tc.post("/control/voice", data=WAV, headers=auth)
        assert resp.status == 503
        assert (await resp.json())["error"] == "voice-disabled"
    finally:
        await tc.close()


async def test_voice_empty_body_never_reaches_the_transcriber(
    client, service, guild_id, auth, transcriber
):
    """A zero-byte upload cannot contain speech — reject it before paying for
    an OpenAI call."""
    put_user_in_voice(service, guild_id)
    resp = await client.post("/control/voice", data=b"", headers=auth)
    assert resp.status == 400
    assert (await resp.json())["error"] == "no-audio"
    assert transcriber.calls == []


async def test_an_empty_upload_is_distinguishable_from_an_unrecognised_one(
    client, service, guild_id, auth, transcriber, interpreter
):
    """The client-side capture failure that took two rounds to find: the plugin
    said "Didn't catch that" because an empty upload answered the same 422 as
    speech nobody could resolve. The key blamed the user's voice for a
    microphone that never opened. The two must not share a code — and the empty
    one has nothing to echo, because it never reached transcription."""
    put_user_in_voice(service, guild_id)

    empty = await client.post("/control/voice?debug=1", data=b"", headers=auth)

    transcriber.text = "mmm hmm yeah whatever"
    interpreter.actions = []
    unresolved = await client.post("/control/voice?debug=1", data=WAV, headers=auth)

    assert empty.status != unresolved.status
    assert (await empty.json())["error"] != (await unresolved.json())["error"]
    assert unresolved.status == 422
    # Only the utterance that actually existed is echoed to Discord.
    posts = debug_posts(service)
    assert len(posts) == 1
    assert "mmm hmm yeah whatever" in posts[0]["text"]


async def test_voice_volume_logs_the_resulting_level(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    """Both directions logging command="volume", args="" would dedupe into one
    row AND retrigger nothing (listener.py: `elif command == "volume" and
    args`). Logging the resulting level fixes both."""
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {"volume": 50})
    transcriber.text = "louder"
    interpreter.actions = [Action("volume", delta=10)]
    resp = await client.post("/control/voice", data=WAV, headers=auth)
    assert resp.status == 200
    entry = service.repo.command_log[-1]
    assert entry[1] == "volume"
    assert entry[2] == "60"
    assert entry[5] == "louder"


# ── grammar first: the interpreter only fills the gap ────────────────────


async def test_grammar_resolved_speech_never_reaches_the_interpreter(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    """The main win, and the one worth a dedicated assertion: a command the
    deterministic grammar understood costs no LLM call, no latency, and gives
    no model the chance to override it. "next song" is the case that used to
    become a SEARCH for the word "song"."""
    put_user_in_voice(service, guild_id)
    transcriber.text = "next song"
    interpreter.actions = [Action("play", query="song", placement="now")]
    body = await (await client.post("/control/voice", data=WAV, headers=auth)).json()
    assert [a["action"] for a in body["actions"]] == ["skip"]
    assert interpreter.calls == [], "the grammar had already decided"
    assert service.node.updates[-1] == (guild_id, {"track": {"encoded": None}})


async def test_unresolved_speech_reaches_the_interpreter_with_the_keywords(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    put_user_in_voice(service, guild_id)
    transcriber.text = "could you maybe play something upbeat"
    interpreter.actions = [Action("pause")]
    resp = await client.post("/control/voice", data=WAV, headers=auth)
    assert resp.status == 200
    assert interpreter.calls == [
        ("could you maybe play something upbeat", ["play"])
    ]


async def test_nothing_resolvable_is_422_and_dispatches_nothing(
    service, store, guild_id, auth, transcriber, interpreter
):
    """The unknown case: the grammar declined, the model declined, so nothing
    runs. There is no longer any path from an unrecognised utterance to a
    search."""
    dispatcher = RecordingDispatcher()
    tc = build_client(
        service, store, transcriber=transcriber, interpreter=interpreter,
        voice_dispatcher=dispatcher,
    )
    await tc.start_server()
    try:
        put_user_in_voice(service, guild_id)
        transcriber.text = "mmm hmm yeah whatever"
        interpreter.actions = []
        before = len(service.repo.command_log)
        resp = await tc.post("/control/voice", data=WAV, headers=auth)
        assert resp.status == 422
        assert (await resp.json())["error"] == "no-speech"
        assert dispatcher.dispatched == []
        assert len(service.repo.command_log) == before
    finally:
        await tc.close()


async def test_interpreter_output_is_run_through_enforce_intent(
    service, store, guild_id, auth, transcriber, interpreter
):
    """The model asked to interrupt what is playing, but the user never said
    "play" — so the action reaches the dispatcher appended, not immediate."""
    dispatcher = RecordingDispatcher()
    tc = build_client(
        service, store, transcriber=transcriber, interpreter=interpreter,
        voice_dispatcher=dispatcher,
    )
    await tc.start_server()
    try:
        put_user_in_voice(service, guild_id)
        transcriber.text = "how about some jazz"
        interpreter.actions = [Action("play", query="jazz", placement="now")]
        resp = await tc.post("/control/voice", data=WAV, headers=auth)
        assert resp.status == 200
        assert dispatcher.dispatched == [
            [Action("play", query="jazz", placement="end")]
        ]
    finally:
        await tc.close()


async def test_the_request_language_reaches_enforce_intent(
    service, store, guild_id, auth, transcriber, interpreter
):
    """A Korean "play" must be able to replace the current track. With the
    rule hardcoded to English this downgraded to "end" and the key still
    reported success — the bot simply appeared to ignore the user."""
    dispatcher = RecordingDispatcher()
    tc = build_client(
        service, store, transcriber=transcriber, interpreter=interpreter,
        voice_dispatcher=dispatcher,
    )
    await tc.start_server()
    try:
        put_user_in_voice(service, guild_id)
        transcriber.text = "보헤미안 랩소디 재생해줘"
        interpreter.actions = [Action("play", query="보헤미안 랩소디", placement="now")]
        resp = await tc.post(
            "/control/voice?language=ko", data=WAV, headers=auth
        )
        assert resp.status == 200
        assert dispatcher.dispatched == [
            [Action("play", query="보헤미안 랩소디", placement="now")]
        ]
    finally:
        await tc.close()


async def test_the_language_query_parameter_reaches_the_transcriber(
    client, service, guild_id, auth, transcriber, interpreter
):
    put_user_in_voice(service, guild_id)
    await client.post("/control/voice?language=ko", data=WAV, headers=auth)
    assert transcriber.languages == ["ko"]


async def test_an_unknown_or_absent_language_becomes_english(
    client, service, guild_id, auth, transcriber, interpreter
):
    """A bad key setting must degrade, not break the key."""
    put_user_in_voice(service, guild_id)
    await client.post("/control/voice?language=klingon", data=WAV, headers=auth)
    await client.post("/control/voice", data=WAV, headers=auth)
    assert transcriber.languages == ["en", "en"]


# ── LLM interpretation: action lists, fallback, partial results ──────────


async def test_voice_runs_every_action_in_order(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    put_user_in_voice(service, guild_id)
    transcriber.text = "skip then pause"
    interpreter.actions = [Action("skip"), Action("pause")]
    body = await (await client.post("/control/voice", data=WAV, headers=auth)).json()
    assert body["ok"] is True
    assert [a["action"] for a in body["actions"]] == ["skip", "pause"]
    assert (await service.repo.get_state(sid))["isPaused"] is True


async def test_voice_logs_one_history_row_per_action_sharing_the_transcript(
    client, service, guild_id, auth, transcriber, interpreter
):
    put_user_in_voice(service, guild_id)
    transcriber.text = "skip then pause"
    interpreter.actions = [Action("skip"), Action("pause")]
    await client.post("/control/voice", data=WAV, headers=auth)
    rows = service.repo.command_log[-2:]
    assert [r[1] for r in rows] == ["skip", "pause"]
    assert all(r[4] == "voice" and r[5] == "skip then pause" for r in rows)


async def test_an_llm_outage_costs_reasoning_not_the_key(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    """The grammar IS the fallback now, so it runs whether or not OpenAI is
    up — an outage costs the reasoning layer, not the closed vocabulary."""
    put_user_in_voice(service, guild_id)
    interpreter.error = InterpretError("down")
    transcriber.text = "skip"
    resp = await client.post("/control/voice", data=WAV, headers=auth)
    assert resp.status == 200
    assert (await resp.json())["actions"][0]["action"] == "skip"
    assert interpreter.calls == []


async def test_an_unresolvable_utterance_during_an_outage_does_nothing(
    client, service, guild_id, auth, transcriber, interpreter
):
    """Degrade to NO actions, not to a search. The route must also not trust
    InterpretError to be the only thing the interpreter can raise."""
    put_user_in_voice(service, guild_id)
    interpreter.error = RuntimeError("boom")
    transcriber.text = "something the grammar has never heard of"
    resp = await client.post("/control/voice", data=WAV, headers=auth)
    assert resp.status == 422
    assert interpreter.calls, "the grammar could not resolve it, so it was asked"


async def test_voice_reports_a_partial_summary(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    from jacky.audio.models import LoadResult

    put_user_in_voice(service, guild_id)
    service.node.load_results["ytsearch:nothing"] = LoadResult(kind="empty", tracks=[])
    await service.repo.update_state(sid, {"currentTrack": {"title": "Now"}})
    transcriber.text = UNINTERPRETABLE
    interpreter.actions = [
        Action("play", query="nothing", placement="end"),
        Action("pause"),
    ]
    body = await (await client.post("/control/voice", data=WAV, headers=auth)).json()
    assert body["ok"] is False
    assert "1 of 2" in body["detail"]


async def test_voice_422_when_nothing_survives_interpretation(
    client, service, guild_id, auth, transcriber, interpreter
):
    put_user_in_voice(service, guild_id)
    interpreter.error = InterpretError("down")
    transcriber.text = "..."      # grammar declines it; there is no fallback
    resp = await client.post("/control/voice", data=WAV, headers=auth)
    assert resp.status == 422


async def test_voice_caps_the_action_list_at_the_route(
    client, service, guild_id, auth, transcriber, interpreter
):
    """The cap is the route's own blast-radius bound, not a favour the
    interpreter does it. `interpreter` is an injected Any: an implementation
    that skipped validate_actions would otherwise get one dispatch and one
    history row per action, unbounded."""
    put_user_in_voice(service, guild_id)
    transcriber.text = "pause " * 9
    interpreter.actions = [Action("pause")] * 9
    before = len(service.repo.command_log)
    body = await (await client.post("/control/voice", data=WAV, headers=auth)).json()
    assert len(body["actions"]) == MAX_ACTIONS
    assert len(service.repo.command_log) - before == MAX_ACTIONS


async def test_voice_interpretation_failure_names_the_exception_type(
    client, service, guild_id, auth, transcriber, interpreter, caplog
):
    """A fixed string made every interpretation failure look alike: OpenAI
    rejecting the request (the feature silently degrading to the fallback
    forever) was indistinguishable from a network partition, while the key
    kept appearing to work. The type and message are transcript-safe —
    test_interpreter_errors_never_carry_the_transcript pins that at the
    source, for every failure mode LlmIntentInterpreter has."""
    put_user_in_voice(service, guild_id)
    transcriber.text = UNINTERPRETABLE
    interpreter.error = InterpretError("interpretation failed: 401")
    with caplog.at_level(0):
        resp = await client.post("/control/voice", data=WAV, headers=auth)
    assert resp.status == 422, "nothing resolved it, so nothing runs"
    assert "InterpretError" in caplog.text
    assert "401" in caplog.text


async def test_voice_transcript_never_reaches_stdout(
    client, service, guild_id, auth, transcriber, interpreter, caplog
):
    """Transcripts are persisted only to the session's command history.
    Container stdout has different retention and different readers."""
    put_user_in_voice(service, guild_id)
    # Deliberately NOT grammar-resolvable: this must exercise the branch that
    # logs an interpreter failure, which is the one place a transcript could
    # plausibly leak into a log line.
    secret = "my deeply private thoughts about someone"
    transcriber.text = secret
    interpreter.error = InterpretError("down")
    with caplog.at_level(0):
        await client.post("/control/voice", data=WAV, headers=auth)
    assert secret not in caplog.text


async def test_voice_response_carries_only_the_real_client_directives(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {"sessionCode": "CODE1234"})
    transcriber.text = UNINTERPRETABLE
    interpreter.actions = [Action("pause"), Action("open_dashboard")]
    body = await (await client.post("/control/voice", data=WAV, headers=auth)).json()
    assert body["client"] == [
        {"type": "open_url", "url": "http://web.test/dashboard/CODE1234"}
    ]


async def test_voice_response_has_an_empty_client_list_when_there_are_none(
    client, service, guild_id, auth, transcriber, interpreter
):
    put_user_in_voice(service, guild_id)
    transcriber.text = UNINTERPRETABLE
    interpreter.actions = [Action("pause")]
    body = await (await client.post("/control/voice", data=WAV, headers=auth)).json()
    assert body["client"] == []


async def test_announce_actions_log_under_their_j_command_names(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    """So the dashboard's history retrigger reaches the matching j! command."""
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {"currentTrack": {"title": "Song"}})
    transcriber.text = UNINTERPRETABLE
    interpreter.actions = [Action("now_playing")]
    await client.post("/control/voice", data=WAV, headers=auth)
    assert service.repo.command_log[-1][1] == "nowplaying"


async def test_open_dashboard_logs_under_its_own_name(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    """It has no j! equivalent — a bot cannot open your browser."""
    put_user_in_voice(service, guild_id)
    transcriber.text = UNINTERPRETABLE
    interpreter.actions = [Action("open_dashboard")]
    await client.post("/control/voice", data=WAV, headers=auth)
    assert service.repo.command_log[-1][1] == "open_dashboard"


# ── the two new inquiries: queue and status through voice ────────────────


async def test_a_grammar_resolved_status_never_reaches_the_interpreter(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    """"status" is in the closed vocabulary now: no LLM call, no latency, and
    the health embed posts where the bot is."""
    put_user_in_voice(service, guild_id)
    transcriber.text = "status"
    body = await (await client.post("/control/voice", data=WAV, headers=auth)).json()
    assert [a["action"] for a in body["actions"]] == ["status_info"]
    assert body["ok"] is True
    assert interpreter.calls == [], "the grammar had already decided"
    embed = service.fake_notifier.sent[-1]["embed"]
    assert embed.title == "🩺 Jacky Music — System Status"


async def test_queue_info_posts_and_logs_one_voice_row_under_queue(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    """The row logs under the j! name "queue" (via _LOG_COMMAND_FOR) with the
    voice source and the transcript — and there is exactly ONE row: the
    Announcer never logs history, the route owns the voice attribution."""
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {"queue": [{"title": "First"}]})
    transcriber.text = "what's in the queue"
    before = len(service.repo.command_log)
    resp = await client.post("/control/voice", data=WAV, headers=auth)
    assert resp.status == 200
    embed = service.fake_notifier.sent[-1]["embed"]
    assert embed.title == "Queue" and "First" in embed.description

    assert len(service.repo.command_log) - before == 1
    entry = service.repo.command_log[-1]
    assert entry[1] == "queue"
    assert entry[4] == "voice"
    assert entry[5] == "what's in the queue"


# ── voice debug echo (?debug=1) ──────────────────────────────────────────


def debug_posts(service):
    """The debug echoes only. `_announce` posts an EMBED, the debug path posts
    TEXT, so the two are distinguishable in the capture notifier."""
    return [s for s in service.fake_notifier.sent if s.get("text") is not None]


async def test_debug_echo_reports_the_transcript_the_provenance_and_the_verbs(
    client, service, guild_id, auth, transcriber, interpreter
):
    put_user_in_voice(service, guild_id)
    transcriber.text = "next song"          # grammar resolves this to `skip`
    resp = await client.post("/control/voice?debug=1", data=WAV, headers=auth)
    assert resp.status == 200
    posts = debug_posts(service)
    assert len(posts) == 1
    message = posts[0]["text"]
    assert posts[0]["guild_id"] == guild_id
    assert "next song" in message      # what was HEARD
    assert "grammar" in message        # how it was RESOLVED
    assert "skip" in message           # what RAN


async def test_no_debug_parameter_posts_nothing(
    client, service, guild_id, auth, transcriber, interpreter
):
    """The regression that keeps the echo opt-in: it publishes transcribed
    speech to a channel other people can read."""
    put_user_in_voice(service, guild_id)
    transcriber.text = "next song"
    resp = await client.post("/control/voice", data=WAV, headers=auth)
    assert resp.status == 200
    assert service.fake_notifier.sent == []


async def test_a_non_truthy_debug_value_leaves_the_echo_off(
    client, service, guild_id, auth, transcriber, interpreter
):
    put_user_in_voice(service, guild_id)
    transcriber.text = "next song"
    for value in ("0", "false", "no", ""):
        resp = await client.post(
            f"/control/voice?debug={value}", data=WAV, headers=auth
        )
        assert resp.status == 200
    assert service.fake_notifier.sent == []


async def test_debug_echo_reports_reasoning_when_the_interpreter_resolved_it(
    client, service, guild_id, auth, transcriber, interpreter
):
    put_user_in_voice(service, guild_id)
    transcriber.text = UNINTERPRETABLE
    interpreter.actions = [Action("pause")]
    resp = await client.post("/control/voice?debug=1", data=WAV, headers=auth)
    assert resp.status == 200
    message = debug_posts(service)[0]["text"]
    assert "reasoning" in message
    assert "grammar" not in message


async def test_debug_echo_posts_when_nothing_was_recognised_and_still_422s(
    client, service, guild_id, auth, transcriber, interpreter
):
    """The single most useful case: "I heard X and resolved nothing". The 422
    returns before dispatch, so the post has to happen anyway."""
    put_user_in_voice(service, guild_id)
    transcriber.text = "mmm hmm yeah whatever"
    interpreter.actions = []
    resp = await client.post("/control/voice?debug=1", data=WAV, headers=auth)
    assert resp.status == 422
    assert (await resp.json())["error"] == "no-speech"
    posts = debug_posts(service)
    assert len(posts) == 1
    assert "mmm hmm yeah whatever" in posts[0]["text"]
    assert "nothing" in posts[0]["text"]


async def test_the_announce_cooldown_does_not_suppress_the_debug_echo(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    """The 10 s per-guild cooldown exists to stop a misrecognition spamming an
    embed. A debug echo is requested per press and is worthless if it drops."""
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {"currentTrack": {"title": "Song"}})
    transcriber.text = "now playing"

    first = await client.post("/control/voice", data=WAV, headers=auth)
    assert first.status == 200
    # A real announce landed, so the cooldown window is now open.
    assert [s for s in service.fake_notifier.sent if s.get("embed") is not None]

    second = await client.post("/control/voice?debug=1", data=WAV, headers=auth)
    assert second.status == 200
    body = await second.json()
    assert body["ok"] is False, "the announce itself was cooled down"
    posts = debug_posts(service)
    assert len(posts) == 1
    assert "now playing" in posts[0]["text"]


async def test_a_failing_debug_post_does_not_change_the_response(
    client, service, guild_id, auth, transcriber, interpreter
):
    class BoomNotifier:
        async def send(self, guild_id, **kwargs):
            raise RuntimeError("discord is having a day")

    put_user_in_voice(service, guild_id)
    service.notifier = BoomNotifier()
    transcriber.text = "next song"
    resp = await client.post("/control/voice?debug=1", data=WAV, headers=auth)
    assert resp.status == 200
    assert [a["action"] for a in (await resp.json())["actions"]] == ["skip"]


# ── POST /control/announce (spec: 2026-08-11-announce-key-design) ────────


ANNOUNCE = "/control/announce"


async def test_announce_409_without_a_session(client, auth):
    resp = await client.post(ANNOUNCE, json={"command": "session"}, headers=auth)
    assert resp.status == 409
    assert (await resp.json()) == {"error": "no-active-session"}


async def test_announce_400_for_an_unknown_or_malformed_command(
    client, service, guild_id, auth
):
    put_user_in_voice(service, guild_id)
    for body in ({"command": "play"}, {"command": 3}, {}, {"cmd": "session"}):
        resp = await client.post(ANNOUNCE, json=body, headers=auth)
        assert resp.status == 400, body
        assert (await resp.json()) == {"error": "unknown-command"}
    assert service.fake_notifier.sent == []


async def test_announce_400_for_a_non_json_body(client, service, guild_id, auth):
    """Malformed body == empty body == no command: a 400, never a 500."""
    put_user_in_voice(service, guild_id)
    resp = await client.post(ANNOUNCE, data=b"not json", headers=auth)
    assert resp.status == 400
    assert (await resp.json()) == {"error": "unknown-command"}


async def test_announce_session_posts_the_session_embed(
    client, service, guild_id, sid, auth
):
    put_user_in_voice(service, guild_id)
    await service.repo.set_session_code(sid, "ABC123")
    resp = await client.post(ANNOUNCE, json={"command": "session"}, headers=auth)
    assert resp.status == 200
    assert (await resp.json()) == {"ok": True, "command": "session"}
    [post] = service.fake_notifier.sent
    assert post["guild_id"] == guild_id
    # The j!session embed, not merely "an embed": the code is in its body.
    assert "ABC123" in post["embed"].description


async def test_announce_nowplaying_posts_the_current_track(
    client, service, guild_id, sid, auth
):
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {
        "currentTrack": {"title": "Song A", "artist": "Artist B", "duration": 63},
    })
    resp = await client.post(ANNOUNCE, json={"command": "nowplaying"}, headers=auth)
    assert (await resp.json()) == {"ok": True, "command": "nowplaying"}
    [post] = service.fake_notifier.sent
    embed = post["embed"]
    assert embed.title == "Now Playing"
    assert "Song A" in embed.description and "Artist B" in embed.description


async def test_announce_queue_posts_the_queue_embed(
    client, service, guild_id, sid, auth
):
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {
        "currentTrack": {"title": "Current"},
        "queue": [{"title": "First", "duration": 61}, {"title": "Second"}],
    })
    resp = await client.post(ANNOUNCE, json={"command": "queue"}, headers=auth)
    assert (await resp.json()) == {"ok": True, "command": "queue"}
    [post] = service.fake_notifier.sent
    embed = post["embed"]
    assert embed.title == "Queue"
    assert "First" in embed.description and "Second" in embed.description
    assert any("Current" in (f.value or "") for f in embed.fields)


async def test_announce_status_posts_the_status_embed(
    client, service, guild_id, sid, auth
):
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {
        "currentTrack": {"title": "Song A", "duration": 100},
        "queue": [{"title": "b"}],
    })
    service.positions[guild_id] = {"connected": True, "position": 5000}
    resp = await client.post(ANNOUNCE, json={"command": "status"}, headers=auth)
    assert (await resp.json()) == {"ok": True, "command": "status"}
    [post] = service.fake_notifier.sent
    embed = post["embed"]
    assert embed.title == "🩺 Jacky Music — System Status"
    fields = {f.name: f.value for f in embed.fields}
    assert "Song A" in fields["This server"]
    # No Status cog on the FakeBot: the uptime line is OMITTED, and the post
    # still happens — a missing cosmetic line must not break a health report.
    assert "up " not in fields["Bot"]


async def test_announce_session_without_a_code_fails_and_posts_nothing(
    client, service, guild_id, auth
):
    put_user_in_voice(service, guild_id)  # init_state leaves sessionCode None
    resp = await client.post(ANNOUNCE, json={"command": "session"}, headers=auth)
    assert resp.status == 200
    assert (await resp.json()) == {"ok": False, "detail": "No session code"}
    assert service.fake_notifier.sent == []


async def test_announce_nowplaying_with_nothing_playing_fails_and_posts_nothing(
    client, service, guild_id, auth
):
    put_user_in_voice(service, guild_id)
    resp = await client.post(ANNOUNCE, json={"command": "nowplaying"}, headers=auth)
    assert resp.status == 200
    assert (await resp.json()) == {"ok": False, "detail": "Nothing is playing"}
    assert service.fake_notifier.sent == []


async def test_announce_of_an_empty_queue_fails_and_posts_nothing(
    client, service, guild_id, sid, auth
):
    """queue_embed would happily render "Queue is empty.", but the key answers
    a person at the deck, not a person in the channel — nothing is posted."""
    put_user_in_voice(service, guild_id)
    assert await service.repo.get_queue(sid) == []
    resp = await client.post(ANNOUNCE, json={"command": "queue"}, headers=auth)
    assert resp.status == 200
    assert (await resp.json()) == {"ok": False, "detail": "Queue is empty"}
    assert service.fake_notifier.sent == []
    assert service.repo.command_log == []


async def test_announce_cooldown_blocks_the_second_post_then_expires(
    client, service, guild_id, sid, auth
):
    from jacky.announce import ANNOUNCE_COOLDOWN_S

    put_user_in_voice(service, guild_id)
    await service.repo.set_session_code(sid, "ABC123")

    first = await client.post(ANNOUNCE, json={"command": "session"}, headers=auth)
    assert first.status == 200 and (await first.json())["ok"] is True

    second = await client.post(ANNOUNCE, json={"command": "session"}, headers=auth)
    assert second.status == 429
    assert (await second.json()) == {"error": "just-posted"}
    assert len(service.fake_notifier.sent) == 1

    announcer = client.server.app["announcer"]
    later = announcer.now() + ANNOUNCE_COOLDOWN_S + 1
    announcer.now = lambda: later
    third = await client.post(ANNOUNCE, json={"command": "session"}, headers=auth)
    assert third.status == 200 and (await third.json())["ok"] is True
    assert len(service.fake_notifier.sent) == 2


async def test_a_failed_announce_burns_neither_history_nor_the_cooldown(
    client, service, guild_id, sid, auth
):
    put_user_in_voice(service, guild_id)
    await service.repo.set_session_code(sid, "ABC123")
    service.fake_notifier.fail = True

    resp = await client.post(ANNOUNCE, json={"command": "session"}, headers=auth)
    assert resp.status == 200
    assert (await resp.json()) == {"ok": False, "detail": "Could not post to Discord"}
    assert service.repo.command_log == []  # the channel never received it

    # No cooldown stamp either: the retry succeeds IMMEDIATELY.
    service.fake_notifier.fail = False
    resp = await client.post(ANNOUNCE, json={"command": "session"}, headers=auth)
    assert (await resp.json())["ok"] is True
    assert len(service.repo.command_log) == 1


async def test_announce_logs_one_history_row_after_a_successful_post(
    client, service, guild_id, sid, auth
):
    put_user_in_voice(service, guild_id)
    await service.repo.set_session_code(sid, "ABC123")
    resp = await client.post(ANNOUNCE, json={"command": "session"}, headers=auth)
    assert resp.status == 200
    # (sid, command, args, user, source, transcript) — "session" is the j!
    # name so history renders the row exactly like a typed j!session, and the
    # streamdeck source keeps deck presses out of the typed rows' dedupe.
    assert service.repo.command_log == [
        (sid, "session", "", "Stream Deck", "streamdeck", ""),
    ]


# ── status parity: the cog and the route share one builder ───────────────


class FakeHttpResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeHttpSession:
    def __init__(self, payload, status=200):
        self._response = FakeHttpResponse(payload, status)
        self.urls: list[str] = []

    def get(self, url):
        self.urls.append(url)
        return self._response


class FakeCtx:
    def __init__(self, guild):
        self.guild = guild
        self.sent: list = []

    async def send(self, *, embed=None):
        self.sent.append(embed)


GUARDIAN_PAYLOAD = {
    "canary": {"ok": False, "playbook": "F3", "error": "boom"},
    "activeFailures": ["F3"],
    "uptimeSeconds": 4200,
    "lastProbeAt": "2026-08-13T10:00:00",
    "actions": [
        {"at": "2026-08-13T09:59:00", "playbook": "F3", "action": "alerted"},
    ],
}


async def test_status_parity_between_the_cog_and_the_route(
    client, service, guild_id, sid, auth
):
    """j!status and the announce route call the same builder over the same
    inputs, so the embeds must be EQUAL — pinned so the extraction cannot
    drift back into two constructions that happen to agree."""
    from tests.conftest import FakeSettings

    from jacky.commands.status import Status

    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {
        "currentTrack": {"title": "Song A", "duration": 120},
        "queue": [{"title": "b"}],
        "isPaused": True,
    })
    service.positions[guild_id] = {"connected": True, "position": 65000}

    bot = service.bot
    bot.repo = service.repo        # what the cog reads; the route uses service
    bot.service = service
    bot.settings = FakeSettings()
    bot.http_session = FakeHttpSession(GUARDIAN_PAYLOAD)
    cog = Status(bot)
    bot.cogs["Status"] = cog       # the route reads started_at via get_cog

    ctx = FakeCtx(guild=bot.get_guild(guild_id))
    await cog.status.callback(cog, ctx)
    resp = await client.post(ANNOUNCE, json={"command": "status"}, headers=auth)
    assert resp.status == 200

    [cog_embed] = ctx.sent
    route_embed = service.fake_notifier.sent[-1]["embed"]
    assert route_embed.to_dict() == cog_embed.to_dict()


async def test_announce_reports_empty_content_not_cooldown_inside_the_window(
    client, service, guild_id, sid, auth,
):
    """Mirrors the voice _announce ordering: content checks come BEFORE the
    cooldown, so a press that had nothing to post is told why rather than
    being blamed on a window it never tried to use."""
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(
        sid, {"sessionCode": "CODE1234", "queue": []}
    )
    first = await client.post(
        "/control/announce", json={"command": "session"}, headers=auth
    )
    assert first.status == 200 and (await first.json())["ok"] is True
    # Window is now burnt; an empty-queue press must still say so.
    resp = await client.post(
        "/control/announce", json={"command": "queue"}, headers=auth
    )
    assert resp.status == 200
    assert (await resp.json()) == {"ok": False, "detail": "Queue is empty"}


async def test_a_voice_announce_cools_down_the_post_key(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    """The ONE shared window (spec: 2026-08-14-voice-announce-unification):
    the route and the voice dispatcher post through the same Announcer, so a
    spoken "session code" blocks an immediate Post key press. Previously
    these were two independent 10 s windows on the same channel."""
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {
        "sessionCode": "ABC123", "currentTrack": {"title": "Song"},
    })

    transcriber.text = "session code"   # grammar-resolved -> session_info
    voice = await client.post("/control/voice", data=WAV, headers=auth)
    assert voice.status == 200
    assert (await voice.json())["ok"] is True
    assert len(service.fake_notifier.sent) == 1

    resp = await client.post(ANNOUNCE, json={"command": "nowplaying"}, headers=auth)
    assert resp.status == 429
    assert (await resp.json()) == {"error": "just-posted"}
    assert len(service.fake_notifier.sent) == 1
