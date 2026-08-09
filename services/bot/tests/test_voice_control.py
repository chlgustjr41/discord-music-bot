"""Voice dispatch onto PlayerService, and voice command-history logging."""

import pytest

from jacky.api.voice_actions import Action
from jacky.audio.models import LoadResult
from tests.conftest import FakeRepo


async def test_fake_repo_records_source_and_transcript():
    repo = FakeRepo()
    await repo.log_command("1", "play", "x", "Me", "42")
    await repo.log_command(
        "1", "play", "x", "Me", "42", source="voice", transcript="play x"
    )
    assert repo.command_log[0][4:] == ("discord", "")
    assert repo.command_log[1][4:] == ("voice", "play x")


# ── the real repository's dedupe ─────────────────────────────────────────
# ServerRepository takes an injected `db` and _log_command is plain sync code,
# so the dedupe can be pinned against production code with no emulator.


class FakeDoc:
    def __init__(self, data):
        self._data, self.reference = data, self

    def to_dict(self):
        return dict(self._data)

    def update(self, patch):
        # firestore.Increment is an opaque sentinel under a fake db; keep the
        # old value rather than pretending to increment.
        self._data.update({k: v for k, v in patch.items() if k != "callCount"})


class FakeColl:
    def __init__(self):
        self.docs, self._filters = [], []

    def where(self, field, _op, value):
        c = FakeColl()
        c.docs = self.docs
        c._filters = [*self._filters, (field, value)]
        return c

    def limit(self, _n):
        return self

    def stream(self):
        return (
            d for d in self.docs
            if all(d.to_dict().get(f) == v for f, v in self._filters)
        )

    def add(self, data):
        self.docs.append(FakeDoc(dict(data)))


class FakeServerDoc:
    """servers/{sid} — its only job is to hand back the subcollection."""

    def __init__(self, coll):
        self._coll = coll

    def collection(self, _name):
        return self._coll


class FakeDb:
    """Collapses collection("servers").document(sid).collection("commandHistory")
    down to one collection — the only path _log_command walks."""

    def __init__(self, coll):
        self._doc = FakeServerDoc(coll)

    def collection(self, _name):
        return self

    def document(self, _name):
        return self._doc


async def test_voice_and_discord_rows_stay_separate():
    """The dedupe filters `source` in PYTHON, not with a third Firestore
    `where`: an equality filter never matches documents missing the field, and
    every row written before this feature has no `source`. Without the split a
    voice "play X" merges into the Discord row and relabels it."""
    from jacky.state.repository import ServerRepository

    coll = FakeColl()
    # A legacy row predating the feature: no `source` field at all.
    coll.add({"command": "play", "args": "x", "callCount": 1})
    repo = ServerRepository(FakeDb(coll))

    await repo.log_command("1", "play", "x", "Me", "42")             # discord
    assert len(coll.docs) == 1, "legacy row must still dedupe"
    assert coll.docs[0].to_dict()["source"] == "discord", "backfilled"

    await repo.log_command(
        "1", "play", "x", "Me", "42", source="voice", transcript="play x"
    )
    assert len(coll.docs) == 2, "voice must not merge into the discord row"
    assert [d.to_dict()["source"] for d in coll.docs] == ["discord", "voice"]

    await repo.log_command("1", "play", "x", "Me", "42")             # discord again
    assert len(coll.docs) == 2, "must increment the discord row, not add a third"


# ── dispatcher ───────────────────────────────────────────────────────────


@pytest.fixture
def dispatcher(service):
    from jacky.voice_control import VoiceIntentDispatcher

    return VoiceIntentDispatcher(service, service.repo)


async def test_play_now_replaces_the_current_track(dispatcher, service, guild_id, sid):
    """'play X' interrupts. The interrupted track is dropped, not requeued —
    the user said skip-and-play."""
    await service.repo.update_state(sid, {"currentTrack": {"title": "Old"}})
    result = (await dispatcher.dispatch_all(guild_id, [
        Action("play", query="a song", placement="now")
    ]))[0]
    assert result.ok
    state = await service.repo.get_state(sid)
    assert state["currentTrack"]["title"] == "Song"
    assert state["queue"] == []


async def test_play_next_jumps_the_queue_without_interrupting(
    dispatcher, service, guild_id, sid
):
    await service.repo.update_state(
        sid, {"currentTrack": {"title": "Now"}, "queue": [{"title": "Old"}]}
    )
    before = len(service.node.updates)
    result = (await dispatcher.dispatch_all(guild_id, [
        Action("play", query="a song", placement="next")
    ]))[0]
    assert result.ok
    queue = (await service.repo.get_state(sid))["queue"]
    assert [t["title"] for t in queue] == ["Song", "Old"]
    assert len(service.node.updates) == before, "must not interrupt"


async def test_play_end_appends(dispatcher, service, guild_id, sid):
    await service.repo.update_state(
        sid, {"currentTrack": {"title": "Now"}, "queue": [{"title": "Old"}]}
    )
    await dispatcher.dispatch_all(guild_id, [
        Action("play", query="a song", placement="end")
    ])
    queue = (await service.repo.get_state(sid))["queue"]
    assert [t["title"] for t in queue] == ["Old", "Song"]


async def test_skip_count_pops_exactly_and_skips_once(
    dispatcher, service, guild_id, sid
):
    """Repeated skip() would race the TrackEnd auto-advance and drop an
    unpredictable number; popping count-1 first makes it exact."""
    await service.repo.update_state(sid, {
        "currentTrack": {"title": "Now"},
        "queue": [{"title": "A"}, {"title": "B"}, {"title": "C"}],
    })
    before = len(service.node.updates)
    result = (await dispatcher.dispatch_all(guild_id, [Action("skip", count=3)]))[0]
    assert result.ok
    queue = (await service.repo.get_state(sid))["queue"]
    assert [t["title"] for t in queue] == ["C"]
    assert len(service.node.updates) == before + 1, "exactly one skip issued"


async def test_shuffle_and_clear_queue(dispatcher, service, guild_id, sid):
    await service.repo.update_state(sid, {"queue": [{"title": "A"}, {"title": "B"}]})
    assert (await dispatcher.dispatch_all(guild_id, [Action("shuffle")]))[0].ok

    assert (await dispatcher.dispatch_all(guild_id, [Action("clear_queue")]))[0].ok
    assert (await service.repo.get_state(sid))["queue"] == []


async def test_absolute_volume_and_loop(dispatcher, service, guild_id, sid):
    result = (await dispatcher.dispatch_all(guild_id, [
        Action("volume", level=42)
    ]))[0]
    assert result.ok
    assert (await service.repo.get_state(sid))["volume"] == 42
    assert result.log_arg == "42"

    await dispatcher.dispatch_all(guild_id, [Action("loop", mode="queue")])
    assert (await service.repo.get_state(sid))["loopMode"] == "queue"


async def test_pause_and_resume_toggle_the_player(dispatcher, service, guild_id, sid):
    assert (await dispatcher.dispatch_all(guild_id, [Action("pause")]))[0].ok
    assert (await service.repo.get_state(sid))["isPaused"] is True

    assert (await dispatcher.dispatch_all(guild_id, [Action("resume")]))[0].ok
    assert (await service.repo.get_state(sid))["isPaused"] is False


async def test_relative_volume_steps_from_the_current_level(
    dispatcher, service, guild_id, sid
):
    """No level, only a delta: the new value must be read off the session
    rather than a fixed default, so up and down are symmetric."""
    await service.repo.update_state(sid, {"volume": 50})
    await dispatcher.dispatch_all(guild_id, [Action("volume", delta=10)])
    assert (await service.repo.get_state(sid))["volume"] == 60
    await dispatcher.dispatch_all(guild_id, [Action("volume", delta=-10)])
    assert (await service.repo.get_state(sid))["volume"] == 50


async def test_playlist_now_jumps_to_the_front(dispatcher, service, guild_id, sid):
    await service.repo.save_playlist(
        sid, "Chill Vibes", [{"title": "P1"}, {"title": "P2"}], "me"
    )
    await service.repo.update_state(
        sid, {"queue": [{"title": "Old"}], "currentTrack": {"title": "Now"}}
    )
    # Spoken loosely: normalization must still find "Chill Vibes".
    result = (await dispatcher.dispatch_all(guild_id, [
        Action("playlist", name="chill vibes", placement="now")
    ]))[0]
    assert result.ok
    queue = (await service.repo.get_state(sid))["queue"]
    assert [t["title"] for t in queue] == ["P1", "P2", "Old"]
    assert service.node.updates[-1] == (guild_id, {"track": {"encoded": None}})


async def test_playlist_end_appends_without_interrupting(
    dispatcher, service, guild_id, sid
):
    await service.repo.save_playlist(sid, "Chill", [{"title": "P1"}], "me")
    await service.repo.update_state(
        sid, {"queue": [{"title": "Old"}], "currentTrack": {"title": "Now"}}
    )
    before = len(service.node.updates)
    result = (await dispatcher.dispatch_all(guild_id, [
        Action("playlist", name="chill", placement="end")
    ]))[0]
    assert result.ok
    queue = (await service.repo.get_state(sid))["queue"]
    assert [t["title"] for t in queue] == ["Old", "P1"]
    # Appending must never interrupt what is playing.
    assert len(service.node.updates) == before


async def test_unknown_playlist_is_not_ok(dispatcher, service, guild_id):
    result = (await dispatcher.dispatch_all(guild_id, [
        Action("playlist", name="nope")
    ]))[0]
    assert result.ok is False
    assert "nope" in result.detail


async def test_an_unrecognized_verb_is_not_ok(dispatcher, guild_id):
    """validate_actions should make this unreachable, but the dispatcher is
    the last line: an unknown verb must be inert, never an exception."""
    result = (await dispatcher.dispatch_all(guild_id, [Action("stop")]))[0]
    assert result.ok is False


async def test_volume_zero_is_an_absolute_level_not_an_unset_one(
    dispatcher, service, guild_id, sid
):
    """"mute" is level=0. A falsy check would read that as "no level given"
    and take the relative branch, bumping the session to 90 instead."""
    result = (await dispatcher.dispatch_all(guild_id, [Action("volume", level=0)]))[0]
    assert result.ok
    assert (await service.repo.get_state(sid))["volume"] == 0


async def test_actions_run_in_order_and_a_failure_does_not_block_the_rest(
    dispatcher, service, guild_id, sid
):
    service.node.load_results["ytsearch:nothing"] = LoadResult(kind="empty", tracks=[])
    await service.repo.update_state(sid, {"currentTrack": {"title": "Now"}})
    results = await dispatcher.dispatch_all(guild_id, [
        Action("play", query="nothing", placement="end"),
        Action("pause"),
    ])
    assert [r.ok for r in results] == [False, True]
    assert (await service.repo.get_state(sid))["isPaused"] is True


async def test_a_raising_action_is_contained(dispatcher, service, guild_id):
    """One exploding action must not abort the batch."""
    async def boom(*_a, **_k):
        raise RuntimeError("firestore down")

    service.repo.shuffle_queue = boom   # the dispatcher calls the repo, not the service
    results = await dispatcher.dispatch_all(guild_id, [
        Action("shuffle"), Action("pause"),
    ])
    assert results[0].ok is False
    assert results[1].ok is True
