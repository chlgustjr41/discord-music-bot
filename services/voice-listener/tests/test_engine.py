from ears.engine import SpeakerEngine
from ears.intents import Intent


class FakeRec:
    """Scripted recognizer: yields queued finals, one per feed() call."""
    def __init__(self):
        self.finals: list[str] = []
        self.grammar_resets = 0
    def accept(self, pcm: bytes) -> str | None:
        return self.finals.pop(0) if self.finals else None
    def reset(self) -> None:
        self.grammar_resets += 1


def make_engine(**kw):
    passive, active = FakeRec(), FakeRec()
    eng = SpeakerEngine(
        passive=passive, active=active, wake_phrase="hey jacky",
        active_window_seconds=5.0, clock=lambda: kw.get("now", [0.0])[0],
    )
    return eng, passive, active, kw.get("now", [0.0])


def test_wake_then_command():
    eng, passive, active, now = make_engine(now=[0.0])
    passive.finals = ["hey jacky"]
    assert eng.feed(b"..") == ("wake", None)          # ack tone cue
    active.finals = ["skip"]
    assert eng.feed(b"..") == ("intent", Intent("skip", None))
    assert eng.state == "passive"                     # returns after a command


def test_ignores_non_wake_speech():
    eng, passive, _, _ = make_engine()
    passive.finals = ["[unk] something else"]
    assert eng.feed(b"..") is None
    assert eng.state == "passive"


def test_active_window_times_out():
    now = [0.0]
    eng, passive, active, _ = make_engine(now=now)
    passive.finals = ["hey jacky"]
    eng.feed(b"..")
    now[0] = 6.0                                       # past 5s window
    assert eng.feed(b"..") == ("timeout", None)
    assert eng.state == "passive"


def test_garbage_in_active_window_is_error():
    eng, passive, active, _ = make_engine()
    passive.finals = ["hey jacky"]
    eng.feed(b"..")
    active.finals = ["[unk] [unk]"]
    assert eng.feed(b"..") == ("error", None)          # buzz cue
    assert eng.state == "passive"
