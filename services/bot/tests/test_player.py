"""PlayerService orchestration against all-fake dependencies."""

from jacky.audio.models import LoadResult
from tests.conftest import make_track


async def test_play_next_pops_queue_and_issues_play(service, guild_id, sid):
    await service.repo.add_to_queue(sid, {
        "title": "Song", "artist": "Artist", "url": "https://youtu.be/abc123",
        "thumbnail": "", "duration": 0, "requestedBy": "Jacob",
    })
    await service.play_next(guild_id)

    state = await service.repo.get_state(sid)
    assert state["queue"] == []
    assert state["isPlaying"] is True
    assert state["currentTrack"]["title"] == "Song"
    assert "startedAt" in state["currentTrack"]

    gid, payload = service.node.updates[-1]
    assert gid == guild_id
    assert payload["track"]["encoded"] == "ENC1"
    assert payload["volume"] == 80
    # Firestore-first ordering: music history logged before/with the play.
    assert service.repo.music_log


async def test_play_next_empty_queue_goes_idle(service, guild_id, sid):
    await service.play_next(guild_id)
    state = await service.repo.get_state(sid)
    assert state["isPlaying"] is False and state["currentTrack"] is None
    assert guild_id in service.idle_tasks
    assert service.node.updates == []


async def test_track_end_advances_and_respects_loop_track(service, guild_id, sid):
    current = {"title": "Song", "artist": "Artist", "url": "https://youtu.be/abc123",
               "thumbnail": "", "duration": 180, "requestedBy": "Jacob"}
    await service.repo.set_current_track(sid, current)
    await service.repo.update_state(sid, {"loopMode": "track"})

    await service.on_track_end(guild_id, "finished")
    # Loop=track: same track re-played, queue untouched.
    assert service.node.updates[-1][1]["track"]["encoded"] == "ENC1"
    assert (await service.repo.get_state(sid))["queue"] == []


async def test_track_end_loop_queue_requeues_current(service, guild_id, sid):
    current = {"title": "Song", "artist": "Artist", "url": "https://youtu.be/abc123",
               "thumbnail": "", "duration": 180, "requestedBy": "Jacob"}
    await service.repo.set_current_track(sid, current)
    await service.repo.update_state(sid, {"loopMode": "queue", "queue": []})

    await service.on_track_end(guild_id, "finished")
    state = await service.repo.get_state(sid)
    # Current went to the back of the queue, then play_next popped it again.
    assert state["currentTrack"]["title"] == "Song"


async def test_track_end_ignores_replaced_and_stopping(service, guild_id, sid):
    await service.repo.update_state(sid, {"queue": [{"title": "Next", "url": "u"}]})
    await service.on_track_end(guild_id, "replaced")
    assert service.node.updates == []

    service._stopping.add(guild_id)
    await service.on_track_end(guild_id, "finished")
    assert service.node.updates == []


async def test_repeated_resolve_failures_halt_and_preserve_queue(service, guild_id, sid):
    service.node.fail_loads = True
    tracks = [{"title": f"T{i}", "artist": "", "url": f"https://youtu.be/{i}",
               "thumbnail": "", "duration": 0, "requestedBy": ""} for i in range(5)]
    await service.repo.update_state(sid, {"queue": list(tracks)})

    await service.play_next(guild_id)

    state = await service.repo.get_state(sid)
    # Breaker at 3 failures: the third track went back to the front,
    # remaining queue intact, playback halted, user notified.
    assert state["isPlaying"] is False
    assert [t["title"] for t in state["queue"]] == ["T2", "T3", "T4"]
    assert any(m.get("error") for m in service.fake_notifier.sent)


async def test_teardown_requeues_current_and_clears_session(service, guild_id, sid):
    current = {"title": "Song", "url": "u", "startedAt": "2026-07-03T00:00:00+00:00"}
    await service.repo.set_current_track(sid, current)
    await service.repo.update_state(
        sid, {"voiceChannelId": "42", "queue": [{"title": "Next", "url": "u2"}]}
    )

    await service.teardown_session(guild_id, requeue_current=True)

    state = await service.repo.get_state(sid)
    assert state["currentTrack"] is None and state["voiceChannelId"] is None
    assert [t["title"] for t in state["queue"]] == ["Song", "Next"]
    assert "startedAt" not in state["queue"][0]
    assert service.bot.get_guild(guild_id).voice_client.disconnected


async def test_resume_track_estimates_position_from_started_at(service, guild_id):
    import datetime
    from datetime import timezone

    started = datetime.datetime.now(timezone.utc) - datetime.timedelta(seconds=30)
    td = {"title": "Song", "url": "https://youtu.be/abc123",
          "startedAt": started.isoformat()}
    ok = await service.resume_track(guild_id, td)
    assert ok
    _, payload = service.node.updates[-1]
    assert 29000 <= payload["position"] <= 35000


async def test_node_ready_not_resumed_reprimes_active_guilds(service, guild_id, sid):
    import asyncio

    await service.repo.set_current_track(sid, {
        "title": "Song", "url": "https://youtu.be/abc123",
        "startedAt": "2026-07-03T00:00:00+00:00",
    })
    await service.on_node_ready(resumed=False)
    await asyncio.sleep(0.05)  # let the per-guild re-prime task run
    # Voice re-sent + track re-issued on the fresh session.
    assert any("track" in p for _, p in service.node.updates)


async def test_node_ready_resumed_is_a_noop(service):
    await service.on_node_ready(resumed=True)
    assert service.node.updates == []


async def test_skip_stops_track_and_volume_clamps(service, guild_id, sid):
    await service.skip(guild_id)
    assert service.node.updates[-1][1] == {"track": {"encoded": None}}

    vol = await service.set_volume(guild_id, 250)
    assert vol == 100
    assert (await service.repo.get_state(sid))["volume"] == 100


async def test_cycle_loop_mode(service, guild_id, sid):
    assert await service.cycle_loop_mode(guild_id) == "track"
    assert await service.cycle_loop_mode(guild_id) == "queue"
    assert await service.cycle_loop_mode(guild_id) == "off"


async def test_begin_session_sets_code_and_state(service, guild_id, sid):
    from types import SimpleNamespace

    guild = service.bot.get_guild(guild_id)
    voice_channel = SimpleNamespace(id=42, name="General")
    text_channel = SimpleNamespace(id=99)
    code = await service.begin_session(guild, voice_channel, text_channel)

    assert len(code) == 6
    state = await service.repo.get_state(sid)
    assert state["voiceChannelId"] == "42" and state["textChannelId"] == "99"
    assert state["sessionCode"] == code
    assert guild.me.nick == f"Jacky Music · {code}"


async def test_playlist_expansion_from_load_result():
    result = LoadResult.from_response({
        "loadType": "playlist",
        "data": {"info": {"name": "Mix"},
                 "tracks": [make_track(title=f"S{i}") for i in range(3)]},
    })
    assert [t["info"]["title"] for t in result.tracks] == ["S0", "S1", "S2"]
