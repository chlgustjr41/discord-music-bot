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
