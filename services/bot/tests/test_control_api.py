"""Control API: auth, voice-presence session resolution, playback handlers."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests.conftest import FakeMember, FakeVoiceState

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
USER_ID = 42


@pytest.fixture
async def client(service):
    from jacky.api.control import register_control_routes
    from jacky.core.health import build_app

    app = build_app(service.bot, service)
    register_control_routes(app, bot=service.bot, service=service, token=TOKEN)
    tc = TestClient(TestServer(app))
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

async def test_missing_token_is_401(client):
    resp = await client.get(f"/control/now-playing?discordUserId={USER_ID}")
    assert resp.status == 401


async def test_wrong_token_is_401(client):
    resp = await client.get(
        f"/control/now-playing?discordUserId={USER_ID}",
        headers={"Authorization": "Bearer nope"},
    )
    assert resp.status == 401


async def test_health_stays_unauthenticated(client):
    resp = await client.get("/health")
    assert resp.status == 200


def test_register_rejects_empty_token(service):
    from aiohttp import web
    from jacky.api.control import register_control_routes

    with pytest.raises(ValueError):
        register_control_routes(
            web.Application(), bot=service.bot, service=service, token=""
        )


# ── session resolution / now-playing ─────────────────────────────────────

async def test_now_playing_inactive_when_user_not_in_voice(client):
    resp = await client.get(f"/control/now-playing?discordUserId={USER_ID}", headers=AUTH)
    assert resp.status == 200
    assert await resp.json() == {"active": False}


async def test_now_playing_inactive_when_bot_has_no_voice_client(client, service, guild_id):
    service.bot.get_guild(guild_id).voice_client = None
    put_user_in_voice(service, guild_id)
    resp = await client.get(f"/control/now-playing?discordUserId={USER_ID}", headers=AUTH)
    assert (await resp.json()) == {"active": False}


async def test_now_playing_reports_current_track(client, service, guild_id, sid):
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {
        "currentTrack": {"title": "Song", "artist": "Artist"},
        "isPaused": False, "volume": 70,
    })
    resp = await client.get(f"/control/now-playing?discordUserId={USER_ID}", headers=AUTH)
    body = await resp.json()
    assert body == {
        "active": True, "title": "Song", "author": "Artist",
        "paused": False, "volume": 70, "guildName": "Guild",
    }


async def test_now_playing_active_but_idle(client, service, guild_id):
    put_user_in_voice(service, guild_id)
    resp = await client.get(f"/control/now-playing?discordUserId={USER_ID}", headers=AUTH)
    body = await resp.json()
    assert body["active"] is True and body["title"] is None


async def test_bad_discord_user_id_is_400(client):
    resp = await client.get("/control/now-playing?discordUserId=notanum", headers=AUTH)
    assert resp.status == 400
    resp = await client.get("/control/now-playing", headers=AUTH)
    assert resp.status == 400


async def test_resolution_picks_guild_where_user_sits_in_voice(client, service, guild_id):
    """Two guilds with live sessions; the user is only in voice in the second."""
    from tests.conftest import FakeGuild, FakeVoice

    other = FakeGuild(id=777, voice_client=FakeVoice(), name="Other")
    service.bot.guilds.insert(0, other)  # scanned first, must NOT match
    await service.repo.init_state("777")
    put_user_in_voice(service, guild_id)
    resp = await client.get(f"/control/now-playing?discordUserId={USER_ID}", headers=AUTH)
    body = await resp.json()
    assert body["guildName"] == "Guild"


# ── actions ──────────────────────────────────────────────────────────────

async def test_actions_409_without_session(client):
    for path in ("/control/play-pause", "/control/skip",
                 "/control/stop", "/control/volume"):
        resp = await client.post(
            path, json={"discordUserId": USER_ID, "delta": 5}, headers=AUTH
        )
        assert resp.status == 409, path


async def test_play_pause_toggles(client, service, guild_id, sid):
    put_user_in_voice(service, guild_id)
    resp = await client.post(
        "/control/play-pause", json={"discordUserId": USER_ID}, headers=AUTH
    )
    assert resp.status == 200 and (await resp.json()) == {"paused": True}
    assert service.node.updates[-1] == (guild_id, {"paused": True})
    assert (await service.repo.get_state(sid))["isPaused"] is True

    resp = await client.post(
        "/control/play-pause", json={"discordUserId": USER_ID}, headers=AUTH
    )
    assert (await resp.json()) == {"paused": False}
    assert service.node.updates[-1] == (guild_id, {"paused": False})


async def test_skip_stops_current_track(client, service, guild_id):
    put_user_in_voice(service, guild_id)
    resp = await client.post(
        "/control/skip", json={"discordUserId": USER_ID}, headers=AUTH
    )
    assert resp.status == 200
    assert service.node.updates[-1] == (guild_id, {"track": {"encoded": None}})


async def test_stop_tears_down_session(client, service, guild_id, sid):
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {"queue": [{"title": "x"}]})
    resp = await client.post(
        "/control/stop", json={"discordUserId": USER_ID}, headers=AUTH
    )
    assert resp.status == 200
    state = await service.repo.get_state(sid)
    assert state["isPlaying"] is False and state["queue"] == []
    assert service.bot.get_guild(guild_id).voice_client is None or \
        service.bot.get_guild(guild_id).voice_client.disconnected


async def test_volume_applies_delta_and_clamps(client, service, guild_id, sid):
    put_user_in_voice(service, guild_id)
    resp = await client.post(
        "/control/volume", json={"discordUserId": USER_ID, "delta": 5}, headers=AUTH
    )
    assert (await resp.json()) == {"volume": 85}  # init_state volume=80

    await service.repo.update_state(sid, {"volume": 98})
    resp = await client.post(
        "/control/volume", json={"discordUserId": USER_ID, "delta": 5}, headers=AUTH
    )
    assert (await resp.json()) == {"volume": 100}


async def test_volume_missing_delta_is_400(client, service, guild_id):
    put_user_in_voice(service, guild_id)
    resp = await client.post(
        "/control/volume", json={"discordUserId": USER_ID}, headers=AUTH
    )
    assert resp.status == 400


async def test_all_control_routes_require_auth(client):
    """Sweep every registered /control/* route: no auth -> 401. Guards against
    a future route being added without the guarded() wrapper."""
    seen = 0
    for resource in client.server.app.router.resources():
        info = resource.get_info()
        path = info.get("path") or info.get("formatter") or ""
        if not path.startswith("/control/"):
            continue
        for route in resource:
            resp = await client.request(route.method, path)
            assert resp.status == 401, f"{route.method} {path}"
            seen += 1
    assert seen >= 5  # now-playing + 4 actions


async def test_member_in_voice_state_without_channel_is_inactive(client, service, guild_id):
    """Transient discord.py state: member has a VoiceState but channel=None."""
    guild = service.bot.get_guild(guild_id)
    guild.members_by_id[USER_ID] = FakeMember(id=USER_ID, voice=FakeVoiceState(channel=None))
    resp = await client.get(f"/control/now-playing?discordUserId={USER_ID}", headers=AUTH)
    assert (await resp.json()) == {"active": False}


async def test_now_playing_survives_null_volume(client, service, guild_id, sid):
    """Web app can write volume: null; must not 500."""
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {"volume": None})
    resp = await client.get(f"/control/now-playing?discordUserId={USER_ID}", headers=AUTH)
    assert resp.status == 200
    assert (await resp.json())["volume"] == 80
