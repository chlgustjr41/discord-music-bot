"""Control API: bearer auth (TokenStore), rate limit, session resolution,
playback handlers, channel discovery."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests.conftest import FakeGuild, FakeMember, FakeVoiceState

USER_ID = 42


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


def build_client(service, store, limiter=None) -> TestClient:
    from jacky.api.control import register_control_routes
    from jacky.api.ratelimit import SlidingWindow
    from jacky.core.health import build_app

    app = build_app(service.bot, service)
    register_control_routes(
        app, bot=service.bot, service=service, token_store=store,
        limiter=limiter or SlidingWindow(limit=1000, window_s=60),
    )
    return TestClient(TestServer(app))


@pytest.fixture
async def client(service, store):
    tc = build_client(service, store)
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
    # now-playing + 4 actions + channels + summon
    # + playlists + playlist + dashboard-url
    assert len(seen_paths) == 10


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
                 "/control/stop", "/control/volume"):
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
                 "/control/stop", "/control/volume"):
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
