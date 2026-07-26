"""Tests for the finalization-driven SpeakerEngine.

The engine feeds every frame to the recognizer and finalizes an utterance after
a run of `silence_end_frames` silent frames, then acts on the result. Fakes let
us script what each recognizer's final() returns without a real Vosk model.
"""

from ears.engine import SpeakerEngine
from ears.intents import Intent


class FakeRec:
    """Records fed frames; final() returns a scripted text once, then None."""

    def __init__(self, final_text: str | None = None):
        self.fed = 0
        self.resets = 0
        self._final = final_text

    def accept(self, pcm: bytes) -> None:
        self.fed += 1

    def final(self) -> str | None:
        t, self._final = self._final, None
        return t

    def reset(self) -> None:
        self.resets += 1


def make(passive_text=None, active_text=None, clk=None, window=5.0, thresh=2):
    clk = clk or [0.0]
    passive, active = FakeRec(passive_text), FakeRec(active_text)
    eng = SpeakerEngine(passive, active, "Hey, Jacky!", window,
                        clock=lambda: clk[0], silence_end_frames=thresh)
    return eng, passive, active, clk


def _utterance(eng, silent_frames=2):
    """Speak one frame, then feed `silent_frames` silent frames; return the last
    event (finalization happens on the silent run)."""
    eng.feed(b"x", silent=False)
    event = None
    for _ in range(silent_frames):
        event = eng.feed(b"", silent=True)
    return event


def test_wake_detected_after_silence_run():
    eng, *_ = make(passive_text="hey jacky", thresh=2)
    assert eng.feed(b"x", silent=False) is None          # speech, no finalize
    assert eng.feed(b"", silent=True) is None             # 1 silent (< thresh)
    assert eng.feed(b"", silent=True) == ("wake", None)   # 2 silent -> finalize
    assert eng.state == "active"


def test_no_wake_when_phrase_absent():
    eng, *_ = make(passive_text="banana bread", thresh=2)
    assert _utterance(eng) is None
    assert eng.state == "passive"


def test_wake_phrase_is_normalized():
    # engine built with "Hey, Jacky!" must match recognizer text "hey jacky"
    eng, *_ = make(passive_text="hey jacky", thresh=2)
    assert _utterance(eng) == ("wake", None)


def test_silence_without_speech_never_finalizes():
    eng, passive, *_ = make(passive_text="hey jacky", thresh=2)
    for _ in range(10):
        assert eng.feed(b"", silent=True) is None
    assert eng.state == "passive"
    assert passive.fed == 10           # still fed, just never finalized


def test_command_recognized_in_active_window():
    eng, _p, _a, _ = make(passive_text="hey jacky", active_text="skip", thresh=2)
    assert _utterance(eng) == ("wake", None)
    assert _utterance(eng) == ("intent", Intent("skip", None))
    assert eng.state == "passive"


def test_active_window_timeout():
    clk = [0.0]
    eng, *_ = make(passive_text="hey jacky", clk=clk, window=5.0, thresh=2)
    assert _utterance(eng) == ("wake", None)
    clk[0] = 6.0
    assert eng.feed(b"x", silent=False) == ("timeout", None)
    assert eng.state == "passive"


def test_active_empty_finalization_is_ignored_not_error():
    eng, *_ = make(passive_text="hey jacky", active_text="[unk]", thresh=2)
    assert _utterance(eng) == ("wake", None)
    assert _utterance(eng) is None     # only noise -> ignored, keep listening
    assert eng.state == "active"


def test_active_unparseable_speech_is_error():
    eng, *_ = make(passive_text="hey jacky", active_text="banana", thresh=2)
    assert _utterance(eng) == ("wake", None)
    assert _utterance(eng) == ("error", None)
    assert eng.state == "passive"


def test_active_recognizer_reset_on_wake():
    eng, _p, active, _ = make(passive_text="hey jacky", thresh=2)
    _utterance(eng)
    assert active.resets == 1           # cleared so the command starts fresh
