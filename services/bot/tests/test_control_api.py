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
