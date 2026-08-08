"""Voice dispatch onto PlayerService, and voice command-history logging."""

import pytest

from jacky.api.voice_intent import Intent
from tests.conftest import FakeRepo


async def test_fake_repo_records_source_and_transcript():
    repo = FakeRepo()
    await repo.log_command("1", "play", "x", "Me", "42")
    await repo.log_command(
        "1", "play", "x", "Me", "42", source="voice", transcript="play x"
    )
    assert repo.command_log[0][4:] == ("discord", "")
    assert repo.command_log[1][4:] == ("voice", "play x")


# ── dispatcher ───────────────────────────────────────────────────────────


@pytest.fixture
def dispatcher(service):
    from jacky.voice_control import VoiceIntentDispatcher

    return VoiceIntentDispatcher(service, service.repo)


async def test_media_intents_call_the_player(dispatcher, service, guild_id, sid):
    await service.repo.update_state(sid, {"volume": 50})

    assert (await dispatcher.dispatch(guild_id, Intent("skip"))).ok
    assert service.node.updates[-1] == (guild_id, {"track": {"encoded": None}})

    assert (await dispatcher.dispatch(guild_id, Intent("pause"))).ok
    assert (await service.repo.get_state(sid))["isPaused"] is True

    assert (await dispatcher.dispatch(guild_id, Intent("resume"))).ok
    assert (await service.repo.get_state(sid))["isPaused"] is False

    await dispatcher.dispatch(guild_id, Intent("volume_up"))
    assert (await service.repo.get_state(sid))["volume"] == 60
    await dispatcher.dispatch(guild_id, Intent("volume_down"))
    assert (await service.repo.get_state(sid))["volume"] == 50


async def test_search_queues_a_track(dispatcher, service, guild_id, sid):
    await service.repo.update_state(sid, {"currentTrack": {"title": "Now"}})
    result = await dispatcher.dispatch(guild_id, Intent("search", "a song"))
    assert result.ok
    assert [t["title"] for t in (await service.repo.get_state(sid))["queue"]] == ["Song"]
    assert result.detail == "Song"


async def test_search_with_no_results_is_not_ok(dispatcher, service, guild_id):
    from jacky.audio.models import LoadResult

    service.node.default_result = LoadResult(kind="empty", tracks=[])
    result = await dispatcher.dispatch(guild_id, Intent("search", "nothing"))
    assert result.ok is False
    assert "No results" in result.detail


async def test_playlist_play_jumps_to_the_front(dispatcher, service, guild_id, sid):
    await service.repo.save_playlist(
        sid, "Chill Vibes", [{"title": "P1"}, {"title": "P2"}], "me"
    )
    await service.repo.update_state(
        sid, {"queue": [{"title": "Old"}], "currentTrack": {"title": "Now"}}
    )
    # Spoken loosely: normalization must still find "Chill Vibes".
    result = await dispatcher.dispatch(guild_id, Intent("playlist_play", "chill vibes"))
    assert result.ok
    queue = (await service.repo.get_state(sid))["queue"]
    assert [t["title"] for t in queue] == ["P1", "P2", "Old"]
    assert service.node.updates[-1] == (guild_id, {"track": {"encoded": None}})


async def test_playlist_add_appends_without_interrupting(
    dispatcher, service, guild_id, sid
):
    await service.repo.save_playlist(sid, "Chill", [{"title": "P1"}], "me")
    await service.repo.update_state(
        sid, {"queue": [{"title": "Old"}], "currentTrack": {"title": "Now"}}
    )
    before = len(service.node.updates)
    result = await dispatcher.dispatch(guild_id, Intent("playlist_add", "chill"))
    assert result.ok
    queue = (await service.repo.get_state(sid))["queue"]
    assert [t["title"] for t in queue] == ["Old", "P1"]
    # Appending must never interrupt what is playing.
    assert len(service.node.updates) == before


async def test_unknown_playlist_is_not_ok(dispatcher, service, guild_id):
    result = await dispatcher.dispatch(guild_id, Intent("playlist_play", "nope"))
    assert result.ok is False
    assert "nope" in result.detail


async def test_stop_intent_is_not_dispatchable(dispatcher, guild_id):
    """stop is excluded from voice by design."""
    result = await dispatcher.dispatch(guild_id, Intent("stop"))
    assert result.ok is False
