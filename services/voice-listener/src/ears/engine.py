"""Per-speaker passive/active recognition state machine.

Events returned by feed():
  ("wake", None)      wake phrase heard -> caller plays ack tone
  ("intent", Intent)  command recognized -> caller ships it + confirm tone
  ("error", None)     active-window speech not understood -> error buzz
  ("timeout", None)   active window expired silently -> passive
  None                nothing notable
"""

import json
import time
from typing import Callable, Protocol

from ears.intents import parse_intent
from ears.phrases import normalize_phrase


class Recognizer(Protocol):
    def accept(self, pcm: bytes) -> str | None: ...
    def reset(self) -> None: ...


class VoskRecognizer:
    """Thin adapter over vosk.KaldiRecognizer (16 kHz, grammar-constrained)."""

    def __init__(self, model, grammar_json: str, sample_rate: int = 16000):
        from vosk import KaldiRecognizer
        self._rec = KaldiRecognizer(model, sample_rate, grammar_json)

    def accept(self, pcm: bytes) -> str | None:
        if self._rec.AcceptWaveform(pcm):
            return json.loads(self._rec.Result()).get("text") or None
        return None

    def reset(self) -> None:
        self._rec.Reset()


class SpeakerEngine:
    def __init__(self, passive: Recognizer, active: Recognizer, wake_phrase: str,
                 active_window_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.passive, self.active = passive, active
        self.wake_phrase = normalize_phrase(wake_phrase)
        self.window = active_window_seconds
        self.clock = clock
        self.state = "passive"
        self._active_until = 0.0

    def feed(self, pcm: bytes):
        if self.state == "passive":
            text = self.passive.accept(pcm)
            if text and self.wake_phrase in text:
                self.state = "active"
                self._active_until = self.clock() + self.window
                self.active.reset()
                return ("wake", None)
            return None
        if self.clock() > self._active_until:
            self.state = "passive"
            return ("timeout", None)
        text = self.active.accept(pcm)
        if text is None:
            return None
        self.state = "passive"
        intent = parse_intent(text.replace("[unk]", " ").strip())
        return ("intent", intent) if intent else ("error", None)
