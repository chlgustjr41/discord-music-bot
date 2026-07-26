from unittest.mock import AsyncMock

import pytest

from jacky.voice_control import VoiceIntentDispatcher


@pytest.fixture
def service():
    s = AsyncMock()
    s.set_volume.side_effect = lambda gid, v: max(0, min(100, v))
    return s


@pytest.fixture
def repo():
    r = AsyncMock()
    r.get_state.return_value = {"volume": 80, "currentTrack": None}
    return r


@pytest.fixture
def dispatcher(service, repo):
    return VoiceIntentDispatcher(service, repo)


async def test_skip(dispatcher, service):
    assert await dispatcher.dispatch(1, "skip", None)
    service.skip.assert_awaited_once_with(1)


async def test_pause_resume(dispatcher, service):
    await dispatcher.dispatch(1, "pause", None)
    service.pause.assert_awaited_with(1, True)
    await dispatcher.dispatch(1, "resume", None)
    service.pause.assert_awaited_with(1, False)


async def test_volume_steps_from_state(dispatcher, service):
    await dispatcher.dispatch(1, "volume_up", None)
    service.set_volume.assert_awaited_with(1, 90)      # 80 + 10
    await dispatcher.dispatch(1, "volume_down", None)
    service.set_volume.assert_awaited_with(1, 70)


async def test_play_starts_when_idle(dispatcher, service):
    # A raw Lavalink track is a dict (to_track_data introspects it); the plan's
    # `object()` sentinel can't survive to_track_data, so use a dict stand-in.
    track = {"info": {"title": "Test Song"}}
    service.resolve.return_value = AsyncMock(
        tracks=[track], first=track, kind="track", playlist_name=None
    )
    assert await dispatcher.dispatch(1, "play", "test song")
    service.start_current_track.assert_awaited_once()


async def test_unknown_intent_rejected(dispatcher):
    assert not await dispatcher.dispatch(1, "reboot", None)
