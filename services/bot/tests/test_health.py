"""GET /health — the guardian's contract — and the server it is mounted on."""

import asyncio
import logging
import time

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from jacky.audio.stats import AudioStats
from jacky.core.health import build_app, start_health_server


@pytest.fixture
async def client(service):
    tc = TestClient(TestServer(build_app(service.bot, service)))
    await tc.start_server()
    yield tc
    await tc.close()


def a_sample(age_s=0.0, **overrides):
    fields = dict(
        sent=2877, nulled=120, deficit=3, playing_players=1,
        lavalink_load=0.04, system_load=0.12,
        received_at=time.monotonic() - age_s,
    )
    return AudioStats(**{**fields, **overrides})


# ── shape ────────────────────────────────────────────────────────────────

async def test_health_before_any_stats_has_a_null_audio_object(client):
    resp = await client.get("/health")
    assert resp.status == 200
    assert await resp.json() == {
        "status": "ok", "guilds": 1, "players": {}, "audio": None,
    }


async def test_health_reports_the_latest_audio_sample(client, service):
    await service.on_stats(a_sample(age_s=12.0))
    body = await (await client.get("/health")).json()

    audio = body["audio"]
    assert set(audio) == {
        "sent", "nulled", "deficit", "playingPlayers",
        "lavalinkLoad", "systemLoad", "ageSeconds",
    }
    assert {k: v for k, v in audio.items() if k != "ageSeconds"} == {
        "sent": 2877, "nulled": 120, "deficit": 3, "playingPlayers": 1,
        "lavalinkLoad": 0.04, "systemLoad": 0.12,
    }
    assert 12.0 <= audio["ageSeconds"] < 14.0
    # Additive: the fields the guardian already reads are untouched.
    assert body["status"] == "ok" and body["guilds"] == 1 and body["players"] == {}


async def test_health_reports_an_idle_sample_with_null_frame_counts(client, service):
    """Lavalink sends no frameStats until a stream has played a full minute.
    The sample is still the latest one — its counts are null, not zero, so a
    reader cannot mistake "not measured" for "no frames lost"."""
    await service.on_stats(a_sample(sent=None, nulled=None, deficit=None,
                                    playing_players=0))
    audio = (await (await client.get("/health")).json())["audio"]
    assert (audio["sent"], audio["nulled"], audio["deficit"]) == (None, None, None)
    assert audio["playingPlayers"] == 0 and audio["lavalinkLoad"] == 0.04


async def test_health_players_keep_every_existing_field_and_gain_ping(
    client, service, guild_id
):
    service.positions[guild_id] = {
        "position": 5000, "connected": True, "time": 1700000000000, "ping": 37,
    }
    service.playing[guild_id] = True
    service.track_ids[guild_id] = "abc123"

    body = await (await client.get("/health")).json()
    assert body["players"] == {str(guild_id): {
        "position": 5000,
        "connected": True,
        "time": 1700000000000,
        "playing": True,
        "trackId": "abc123",
        "ping": 37,
    }}


async def test_health_omits_ping_when_the_player_state_has_none(
    client, service, guild_id
):
    service.positions[guild_id] = {"position": 0, "connected": False}
    body = await (await client.get("/health")).json()
    assert body["players"] == {str(guild_id): {
        "position": 0, "connected": False, "time": 0,
        "playing": False, "trackId": None,
    }}


# ── access log ───────────────────────────────────────────────────────────

async def test_the_health_server_writes_no_access_log(service, caplog):
    """The OAuth routes share this server, and aiohttp's default access log
    writes the full request line — `GET /control/auth/callback?state=…&code=…`
    and `/control/auth/poll?state=…` — to stdout. The `state` is the bearer
    for claiming a freshly minted token, so it must never be logged.

    Asserted on behaviour (a real request, then the captured log) rather than
    on the runner's private attributes, which differ between aiohttp versions.
    """
    app = build_app(service.bot, service)

    async def poll(_request: web.Request) -> web.Response:
        return web.json_response({"status": "pending"}, status=202)

    app.add_routes([web.get("/control/auth/poll", poll)])

    # Root at DEBUG BEFORE the server starts, so aiohttp's "is the access
    # logger enabled?" check cannot be what keeps the log quiet.
    with caplog.at_level(logging.DEBUG):
        runner = await start_health_server(
            service.bot, service, 0, app=app, host="127.0.0.1"
        )
        try:
            port = runner.addresses[0][1]
            url = (f"http://127.0.0.1:{port}/control/auth/poll"
                   "?state=STATE-SECRET-123&code=CODE-SECRET-456")
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    assert resp.status == 202
            # The access log line is written after the response has gone out.
            await asyncio.sleep(0.05)
        finally:
            await runner.cleanup()

    assert "STATE-SECRET-123" not in caplog.text
    assert "CODE-SECRET-456" not in caplog.text
    assert [r for r in caplog.records if r.name == "aiohttp.access"] == []
