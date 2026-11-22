"""Malformed values in client-writable documents must not break playback.

servers/{guildId} and everything under it can be written by any web client
holding a session code, so every field the bot reads back is untrusted. Each
test here writes the kind of value a hostile or buggy client can store and
asserts the bot neither raises nor acts on it. The coercion rules themselves
are pinned in test_untrusted.py.
"""

import asyncio
import logging

import pytest

from jacky.state.listener import ServerDocListener
from jacky.state.summon import SummonWatcher

JUNK_VOLUMES = ["loud", None, {}, [], True, float("nan"), float("inf"),
                # Past the interpreter's int-conversion digit limit: ValueError.
                pytest.param("9" * 5000, id="5000-digit-string")]

SONG = {"title": "Song", "artist": "Artist", "url": "https://youtu.be/abc123",
        "thumbnail": "", "duration": 0, "requestedBy": "Jacob"}


# ── audio/player.py ──────────────────────────────────────────────────────

@pytest.mark.parametrize("volume", JUNK_VOLUMES)
async def test_play_survives_a_junk_volume_and_uses_the_default(
    service, guild_id, sid, volume
):
    """This one is not a single failed request: _issue_play runs for EVERY
    play attempt, so a stored junk volume used to stop all playback in the
    guild until someone rewrote the field."""
    await service.repo.update_state(sid, {"volume": volume, "queue": [dict(SONG)]})

    await service.play_next(guild_id)

    _, payload = service.node.updates[-1]
    assert payload["track"]["encoded"] == "ENC1"
    assert payload["volume"] == 80
    assert (await service.repo.get_state(sid))["isPlaying"] is True


@pytest.mark.parametrize("volume,expected", [
    (0, 0), (35, 35), ("35", 35), (35.6, 35), (250, 100), (-20, 0),
])
async def test_play_clamps_the_stored_volume(service, guild_id, sid, volume, expected):
    await service.repo.update_state(sid, {"volume": volume, "queue": [dict(SONG)]})
    await service.play_next(guild_id)
    assert service.node.updates[-1][1]["volume"] == expected


async def test_play_without_a_volume_field_uses_the_default(service, guild_id, sid):
    state = await service.repo.get_state(sid)
    del state["volume"]
    await service.repo.add_to_queue(sid, dict(SONG))
    await service.play_next(guild_id)
    assert service.node.updates[-1][1]["volume"] == 80


@pytest.mark.parametrize("channel_id", ["garbage", "12abc", {}, [], 4.2, True, "-42"])
async def test_reset_with_a_junk_voice_channel_id_is_refused_not_raised(
    service, guild_id, sid, channel_id
):
    await service.repo.update_state(sid, {"voiceChannelId": channel_id})
    assert await service.reset_session(guild_id) is False
    assert service.node.updates == []


@pytest.mark.parametrize("started_at", [5, {}, [], "yesterday", "2026-07-03T00:00:00"])
async def test_resume_with_a_junk_started_at_starts_from_zero(
    service, guild_id, started_at
):
    """Includes a NAIVE timestamp: subtracting it from an aware `now` raises
    TypeError, which the old `except ValueError` did not cover."""
    ok = await service.resume_track(
        guild_id,
        {"title": "Song", "url": "https://youtu.be/abc123", "startedAt": started_at},
    )
    assert ok
    assert service.node.updates[-1][1]["position"] == 0


@pytest.mark.parametrize("channel_id", ["garbage", "", None, {}, "4.2"])
async def test_summon_handler_with_a_junk_channel_id_is_ignored(
    service, guild_id, sid, channel_id
):
    guild = service.bot.get_guild(guild_id)
    guild.voice_client = None
    await service.repo.update_state(sid, {"summonRequest": {"channelId": channel_id}})

    await service.handle_summon(guild_id, channel_id)

    assert guild.voice_client is None
    assert (await service.repo.get_state(sid))["summonRequest"] is None


# ── startup convergence ──────────────────────────────────────────────────

async def test_convergence_skips_malformed_documents_and_still_converges_the_rest(
    service, guild_id, sid, caplog
):
    """active_server_ids() is every document under servers/ with a truthy
    voiceChannelId — including ones a stranger created under made-up ids."""
    from tests.conftest import FakeMember

    guild = service.bot.get_guild(guild_id)
    guild.voice_client = None
    channel = guild.add_voice_channel(42, name="Lounge")
    channel.members = [FakeMember(id=7)]
    for m in channel.members:
        m.bot = False

    junk_id = "not-a-guild/../<script>"
    service.repo.states = {
        junk_id: {"voiceChannelId": "42"},                       # non-numeric doc id
        "999": {"voiceChannelId": {"nested": "junk-channel"}},   # no such guild, junk channel
        sid: {**service.repo.states[sid], "voiceChannelId": "42",
              "queue": [dict(SONG)]},
    }

    with caplog.at_level(logging.DEBUG):
        await service.converge_on_startup()

    # The real session, listed LAST, was still rebuilt.
    assert guild.voice_client is not None
    assert (await service.repo.get_state(sid))["currentTrack"]["title"] == "Song"
    # Nothing a stranger wrote reached the log.
    assert "not-a-guild" not in caplog.text and "<script>" not in caplog.text
    assert "junk-channel" not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize("channel_id", ["garbage", {"a": 1}, ["x"], 4.2, True])
async def test_convergence_treats_a_junk_voice_channel_id_as_a_stale_session(
    service, guild_id, sid, channel_id, caplog
):
    await service.repo.update_state(sid, {
        "voiceChannelId": channel_id, "currentTrack": dict(SONG), "isPlaying": True,
    })

    with caplog.at_level(logging.DEBUG):
        await service.converge_on_startup()

    state = await service.repo.get_state(sid)
    # Cleared exactly like a session whose channel is gone: nothing playing,
    # the interrupted track back at the head of the queue.
    assert state["voiceChannelId"] is None and state["currentTrack"] is None
    assert [t["title"] for t in state["queue"]] == ["Song"]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# ── state/listener.py ────────────────────────────────────────────────────

@pytest.fixture
def listener(service, sid):
    return ServerDocListener(service.bot, service.repo, service, sid)


@pytest.fixture
def calls(service, monkeypatch):
    """Record set_volume / seek instead of running them."""
    seen = {"volume": [], "seek": []}

    async def set_volume(guild_id, volume):
        seen["volume"].append(volume)
        return volume

    async def seek(guild_id, seconds):
        seen["seek"].append(seconds)

    monkeypatch.setattr(service, "set_volume", set_volume)
    monkeypatch.setattr(service, "seek", seek)
    return seen


@pytest.mark.parametrize("volume", JUNK_VOLUMES)
async def test_a_junk_volume_change_is_ignored(listener, calls, volume):
    await listener._handle_changes({"volume": 80}, {"volume": volume})
    assert calls["volume"] == []


async def test_a_junk_volume_does_not_stop_the_rest_of_the_snapshot(
    listener, service, guild_id, calls
):
    """The handlers after the volume one used to be skipped by its exception."""
    await listener._handle_changes(
        {"volume": 80, "isPaused": False}, {"volume": "loud", "isPaused": True},
    )
    assert calls["volume"] == []
    assert service.node.updates[-1] == (guild_id, {"paused": True})


@pytest.mark.parametrize("volume,expected", [(55, 55), ("55", 55), (0, 0), (400, 100)])
async def test_a_real_volume_change_still_applies(listener, calls, volume, expected):
    await listener._handle_changes({"volume": 80}, {"volume": volume})
    assert calls["volume"] == [expected]


@pytest.mark.parametrize("seek", [
    "soon", {}, [], True, float("nan"), float("inf"), 10**12, 1e300,
    pytest.param("9" * 5000, id="5000-digit-string"),
])
async def test_a_junk_or_absurd_seek_is_ignored(listener, service, sid, calls, seek):
    current = {**SONG, "startedAt": "2026-07-03T00:00:00+00:00"}
    await listener._handle_changes(
        {"currentTrack": current}, {"currentTrack": current, "seekPosition": seek},
    )
    assert calls["seek"] == []
    # startedAt is what the dashboard's progress bar runs on; an ignored seek
    # must not restamp it (the fixture's stored currentTrack is still None).
    state = await service.repo.get_state(sid)
    assert state["currentTrack"] is None and "seekPosition" not in state


async def test_a_negative_seek_goes_to_the_start(listener, calls):
    await listener._handle_changes({}, {"seekPosition": -3})
    assert calls["seek"] == [0]


async def test_a_real_seek_still_applies_and_restamps_started_at(
    listener, service, sid, calls
):
    import datetime
    from datetime import timezone

    current = {**SONG, "startedAt": "2026-07-03T00:00:00+00:00"}
    await listener._handle_changes(
        {"currentTrack": current}, {"currentTrack": current, "seekPosition": 90.7},
    )
    assert calls["seek"] == [90]
    state = await service.repo.get_state(sid)
    assert state["seekPosition"] is None
    started = datetime.datetime.fromisoformat(state["currentTrack"]["startedAt"])
    elapsed = (datetime.datetime.now(timezone.utc) - started).total_seconds()
    assert 89 <= elapsed <= 95


@pytest.mark.parametrize("retrigger", [
    "play", 5, ["play", "x"],
    {"command": "volume", "args": {}},
    {"command": "volume", "args": ["50"]},
    {"command": "volume", "args": "loud"},
    {"command": {"x": 1}, "args": "y"},
])
async def test_a_junk_retrigger_is_consumed_without_raising(
    listener, service, sid, calls, retrigger
):
    await listener._handle_changes({}, {"retriggerCommand": retrigger})
    assert calls["volume"] == []
    assert (await service.repo.get_state(sid))["retriggerCommand"] is None


async def test_a_real_volume_retrigger_still_applies(listener, calls):
    await listener._handle_changes(
        {}, {"retriggerCommand": {"command": "volume", "args": "60"}}
    )
    assert calls["volume"] == [60]


# ── state/summon.py ──────────────────────────────────────────────────────
# _on_snapshot runs on the Firestore SDK's thread over EVERY document in the
# collection, and anyone can create a document there. An exception escaping
# it — or an id left behind in _in_flight — is an outage for every server.

GUILD_A = "123456789012345678"
GUILD_B = "223456789012345678"
CHANNEL = "323456789012345678"


class FakeDoc:
    def __init__(self, doc_id, data, *, explode=False):
        self.id, self._data, self._explode = doc_id, data, explode

    def to_dict(self):
        if self._explode:
            raise RuntimeError("secret-content-in-exception")
        return self._data


class RecordingService:
    def __init__(self):
        self.summons: list[tuple] = []

    async def handle_summon(self, guild_id, channel_id):
        self.summons.append((guild_id, channel_id))


@pytest.fixture
async def watcher():
    from types import SimpleNamespace

    service = RecordingService()
    bot = SimpleNamespace(loop=asyncio.get_running_loop())
    return SummonWatcher(bot, None, service)


async def settle():
    # run_coroutine_threadsafe -> task -> done-callback takes a few loop turns.
    for _ in range(3):
        await asyncio.sleep(0.01)


def request(channel_id=CHANNEL):
    return {"summonRequest": {"channelId": channel_id}}


async def test_a_well_formed_request_is_scheduled_once_and_released(watcher):
    docs = [FakeDoc(GUILD_A, request())]
    watcher._on_snapshot(docs, [], None)
    # Still in flight: a second snapshot of the same doc must not double-join.
    watcher._on_snapshot(docs, [], None)
    await settle()

    assert watcher.service.summons == [(int(GUILD_A), CHANNEL)]
    assert watcher._in_flight == set()


@pytest.mark.parametrize("doc_id", [
    "not-a-guild", "abc", "", "12345", "1" * 23, GUILD_A + "x", " " + GUILD_A,
    "١٢٣٤٥٦٧٨٩٠١٢٣٤٥٦٧٨",
])
async def test_a_malformed_document_id_is_skipped_and_never_held_in_flight(
    watcher, doc_id, caplog
):
    with caplog.at_level(logging.DEBUG):
        watcher._on_snapshot(
            [FakeDoc(doc_id, request()), FakeDoc(GUILD_B, request())], [], None
        )
        await settle()

    # The bad document did not stop the one after it.
    assert watcher.service.summons == [(int(GUILD_B), CHANNEL)]
    assert watcher._in_flight == set()
    if len(doc_id) > 3:
        assert doc_id not in caplog.text


@pytest.mark.parametrize("channel_id", [
    "general", "12abc", "12345", "1" * 23, {"id": CHANNEL}, [CHANNEL], 4.2, True,
    CHANNEL + "; drop",
])
async def test_a_malformed_channel_id_is_skipped_and_never_held_in_flight(
    watcher, channel_id, caplog
):
    with caplog.at_level(logging.DEBUG):
        watcher._on_snapshot(
            [FakeDoc(GUILD_A, request(channel_id)), FakeDoc(GUILD_B, request())],
            [], None,
        )
        await settle()

    assert watcher.service.summons == [(int(GUILD_B), CHANNEL)]
    assert watcher._in_flight == set()
    assert "general" not in caplog.text and "drop" not in caplog.text


@pytest.mark.parametrize("data", [
    {"summonRequest": "join please"},
    {"summonRequest": ["x"]},
    {"summonRequest": 5},
    {"summonRequest": {}},
    {"summonRequest": None},
    {},
    None,
])
async def test_a_document_without_a_usable_request_is_skipped(watcher, data):
    watcher._on_snapshot(
        [FakeDoc(GUILD_A, data), FakeDoc(GUILD_B, request())], [], None
    )
    await settle()
    assert watcher.service.summons == [(int(GUILD_B), CHANNEL)]
    assert watcher._in_flight == set()


async def test_a_document_that_raises_does_not_stop_the_others(watcher, caplog):
    with caplog.at_level(logging.DEBUG):
        watcher._on_snapshot(
            [FakeDoc(GUILD_A, None, explode=True), FakeDoc(GUILD_B, request())],
            [], None,
        )
        await settle()
    assert watcher.service.summons == [(int(GUILD_B), CHANNEL)]
    # The exception's MESSAGE can carry document content; only its type is logged.
    assert "secret-content" not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_a_failed_schedule_does_not_leave_the_id_in_flight(watcher, monkeypatch):
    """If the hop onto the bot loop itself fails (loop closing at shutdown),
    the id must be released — otherwise that server can never be summoned
    again until the process restarts."""
    import jacky.state.summon as summon_mod

    def refuse(coro, loop):
        coro.close()
        raise RuntimeError("loop is closed")

    monkeypatch.setattr(summon_mod.asyncio, "run_coroutine_threadsafe", refuse)
    watcher._on_snapshot(
        [FakeDoc(GUILD_A, request()), FakeDoc(GUILD_B, request())], [], None
    )
    assert watcher._in_flight == set()


async def test_malformed_requests_cannot_flood_the_log(watcher, caplog):
    """The collection listener replays EVERY document on EVERY change to any
    of them, so a junk request that is merely skipped would be re-reported
    for as long as it sits there."""
    docs = [FakeDoc("not-a-guild", request())]
    with caplog.at_level(logging.DEBUG):
        for _ in range(200):
            watcher._on_snapshot(docs, [], None)
    assert len([r for r in caplog.records if r.name == "jacky.summon"]) == 1


# ── queue items ──────────────────────────────────────────────────────────
# The rules can cap the queue's length but cannot look inside its items, so a
# web client can store anything in the list. Advancing the queue happens
# inside a Lavalink event handler, which is the worst place to raise.

JUNK_ITEMS = ["just a string", 7, None, [], {}, True, 3.5, ["nested"]]


@pytest.mark.parametrize("junk", JUNK_ITEMS, ids=repr)
async def test_play_next_skips_a_junk_queue_item_and_plays_the_real_one(
    service, guild_id, sid, junk
):
    """A falsy junk item (None, {}, 0) used to read as "queue is empty" and
    stop playback with real tracks still waiting behind it; a truthy one
    raised AttributeError."""
    await service.repo.update_state(sid, {"queue": [junk, dict(SONG)]})

    await service.play_next(guild_id)

    state = await service.repo.get_state(sid)
    assert service.node.updates[-1][1]["track"]["encoded"] == "ENC1"
    assert state["isPlaying"] is True
    assert state["queue"] == []


async def test_play_next_with_only_junk_ends_like_an_empty_queue(service, guild_id, sid):
    await service.repo.update_state(sid, {"queue": ["a", None, 5, {}]})

    await service.play_next(guild_id)

    state = await service.repo.get_state(sid)
    assert state["isPlaying"] is False
    assert state["currentTrack"] is None
    assert state["queue"] == []
    assert service.node.updates == []


async def test_track_end_with_a_junk_item_next_does_not_raise(service, guild_id, sid):
    await service.repo.update_state(sid, {
        "queue": [{"title": 42, "url": ["x"]}, "junk", dict(SONG)],
        "currentTrack": dict(SONG), "isPlaying": True,
    })

    await service.on_track_end(guild_id, "finished")   # must not raise

    assert (await service.repo.get_state(sid))["queue"] == []
