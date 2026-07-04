"""FUTURE #2 (dashboard summon) and #4 (search semantics) coverage."""

from jacky.audio.models import LoadResult
from tests.conftest import make_track


# ── #4: playlist selected-track ordering ─────────────────────────────────

def test_playlist_selected_track_leads():
    result = LoadResult.from_response({
        "loadType": "playlist",
        "data": {
            "info": {"name": "Mix", "selectedTrack": 2},
            "tracks": [make_track(title=f"S{i}", identifier=f"id{i}") for i in range(4)],
        },
    })
    ordered = [t["info"]["title"] for t in result.tracks_selected_first]
    assert ordered == ["S2", "S0", "S1", "S3"]


def test_playlist_without_selection_keeps_order():
    for selected in (-1, 0, 99):
        result = LoadResult.from_response({
            "loadType": "playlist",
            "data": {
                "info": {"name": "Mix", "selectedTrack": selected},
                "tracks": [make_track(title=f"S{i}") for i in range(3)],
            },
        })
        assert [t["info"]["title"] for t in result.tracks_selected_first] == ["S0", "S1", "S2"]


# ── #2: known-channel memory + summon handling ───────────────────────────

async def test_begin_session_records_known_channels(service, guild_id, sid):
    from types import SimpleNamespace

    guild = service.bot.get_guild(guild_id)
    text = SimpleNamespace(id=99)
    await service.begin_session(guild, SimpleNamespace(id=1, name="General"), text)
    await service.begin_session(guild, SimpleNamespace(id=2, name="Man Cave"), text)
    await service.begin_session(guild, SimpleNamespace(id=1, name="General"), text)

    state = await service.repo.get_state(sid)
    known = state["knownVoiceChannels"]
    # Most-recent-first, deduped by id.
    assert [(c["id"], c["name"]) for c in known] == [("1", "General"), ("2", "Man Cave")]


async def test_summon_joins_channel_and_mints_session(service, guild_id, sid):
    guild = service.bot.get_guild(guild_id)
    guild.voice_client = None
    guild.add_voice_channel(42, name="Lounge")
    await service.repo.update_state(sid, {"textChannelId": "77", "summonRequest": {"channelId": "42"}})

    await service.handle_summon(guild_id, "42")

    state = await service.repo.get_state(sid)
    assert state["summonRequest"] is None
    assert state["voiceChannelId"] == "42"
    assert len(state["sessionCode"]) == 6
    assert state["textChannelId"] == "77"  # prior text channel preserved
    assert guild.voice_client is not None
    assert any("Summoned" in (m.get("text") or "") for m in service.fake_notifier.sent)


async def test_summon_ignored_when_session_active(service, guild_id, sid):
    guild = service.bot.get_guild(guild_id)  # fixture guild already has voice_client
    guild.add_voice_channel(42)
    await service.repo.update_state(sid, {"summonRequest": {"channelId": "42"}})

    await service.handle_summon(guild_id, "42")

    state = await service.repo.get_state(sid)
    assert state["summonRequest"] is None      # request consumed
    assert state.get("sessionCode") is None    # but no new session minted


async def test_summon_ignored_when_not_activated(service, guild_id, sid):
    service.repo.activated = False
    guild = service.bot.get_guild(guild_id)
    guild.voice_client = None
    guild.add_voice_channel(42)

    await service.handle_summon(guild_id, "42")

    state = await service.repo.get_state(sid)
    assert state.get("sessionCode") is None
    assert guild.voice_client is None


async def test_summon_ignored_for_unknown_channel(service, guild_id, sid):
    guild = service.bot.get_guild(guild_id)
    guild.voice_client = None

    await service.handle_summon(guild_id, "404")

    assert guild.voice_client is None
    assert (await service.repo.get_state(sid))["summonRequest"] is None
