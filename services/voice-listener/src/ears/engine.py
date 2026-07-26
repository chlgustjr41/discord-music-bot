"""Per-speaker passive/active recognition state machine.

Vosk's grammar recognizer does NOT reliably endpoint mid-stream (AcceptWaveform
stays False for the whole utterance — verified against real recordings), so we
drive finalization ourselves: feed every frame, and when the speaker pauses (a
short run of silent frames after speech) call FinalResult() to read what was
said. Discord sends ~5 trailing silence frames when a user stops talking, and
voice_recv delivers them for a known ssrc — enough to trip the silence run.

Events returned by feed():
  ("wake", None)      wake phrase heard -> caller plays ack tone
  ("intent", Intent)  command recognized -> caller ships it + confirm tone
  ("error", None)     active-window speech understood but not a command -> buzz
  ("timeout", None)   active window expired -> back to passive
  None                nothing notable this frame
"""

import json
import logging
import time
from typing import Callable, Protocol

from ears.intents import parse_intent
from ears.phrases import normalize_phrase

log = logging.getLogger("ears.engine")

# A run of this many silent frames (20 ms each) ends an utterance and triggers
# finalization. 4 frames (~80 ms) fits inside Discord's ~5 trailing-silence
# frames, yet is long enough not to split "hey jacky" (validated on samples).
SILENCE_END_FRAMES = 4


class Recognizer(Protocol):
    def accept(self, pcm: bytes) -> None: ...
    def final(self) -> str | None: ...
    def reset(self) -> None: ...


class VoskRecognizer:
    """Thin adapter over vosk.KaldiRecognizer (16 kHz, grammar-constrained)."""

    def __init__(self, model, grammar_json: str, sample_rate: int = 16000):
        from vosk import KaldiRecognizer
        self._rec = KaldiRecognizer(model, sample_rate, grammar_json)

    def accept(self, pcm: bytes) -> None:
        # Grammar recognizers don't endpoint reliably, so we ignore
        # AcceptWaveform's return and read the utterance later via final().
        self._rec.AcceptWaveform(pcm)

    def final(self) -> str | None:
        # FinalResult() flushes the accumulated utterance and resets the
        # recognizer, so the next utterance starts clean.
        return json.loads(self._rec.FinalResult()).get("text") or None

    def reset(self) -> None:
        self._rec.Reset()


class SpeakerEngine:
    def __init__(self, passive: Recognizer, active: Recognizer, wake_phrase: str,
                 active_window_seconds: float, clock: Callable[[], float] = time.monotonic,
                 silence_end_frames: int = SILENCE_END_FRAMES, debug: bool = False,
                 label: str = ""):
        self.passive, self.active = passive, active
        self.wake_phrase = normalize_phrase(wake_phrase)
        self.window = active_window_seconds
        self.clock = clock
        self.silence_end_frames = silence_end_frames
        self.debug = debug
        self.label = label      # e.g. "guild/user" for debug logs
        self.state = "passive"
        self._active_until = 0.0
        self._had_speech = False
        self._silence_run = 0

    def feed(self, pcm: bytes, silent: bool):
        # Active window expiry — observable only while frames keep arriving.
        if self.state == "active" and self.clock() > self._active_until:
            self._to_passive()
            return ("timeout", None)

        rec = self.passive if self.state == "passive" else self.active
        rec.accept(pcm)

        if not silent:
            self._had_speech = True
            self._silence_run = 0
            return None

        # A silent frame only matters once the speaker has actually said
        # something this utterance.
        if not self._had_speech:
            return None
        self._silence_run += 1
        if self._silence_run < self.silence_end_frames:
            return None

        # Utterance boundary: finalize and act on what was heard.
        text = rec.final()
        if self.debug:
            log.info("[dbg] %s %s-final=%r (wake=%r)",
                     self.label, self.state, text, self.wake_phrase)
        self._had_speech = False
        self._silence_run = 0
        return self._on_utterance(text)

    def _on_utterance(self, text: str | None):
        if self.state == "passive":
            if text and self.wake_phrase in text:
                self.state = "active"
                self._active_until = self.clock() + self.window
                self.active.reset()
                return ("wake", None)
            return None
        # Active state: parse the command.
        cleaned = (text or "").replace("[unk]", " ").strip()
        if not cleaned:
            return None       # trailing breath/noise — keep listening in-window
        self.state = "passive"
        intent = parse_intent(cleaned)
        return ("intent", intent) if intent else ("error", None)

    def _to_passive(self) -> None:
        self.state = "passive"
        self._had_speech = False
        self._silence_run = 0
