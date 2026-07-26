"""48 kHz stereo s16le (Discord) -> 16 kHz mono s16le (Vosk), plus RMS gate.

audioop is deprecated for 3.13 but we pin python 3.11 in the image; swap to
`audioop-lts` if the base image ever moves.
"""

import audioop

SILENCE_RMS = 200          # empirically well below quiet speech


def is_silence(pcm_48k_stereo: bytes) -> bool:
    return audioop.rms(pcm_48k_stereo, 2) < SILENCE_RMS


class Downsampler:
    """Stateful (ratecv carries filter state between frames); one per speaker."""

    def __init__(self):
        self._state = None

    def feed(self, pcm_48k_stereo: bytes) -> bytes:
        mono = audioop.tomono(pcm_48k_stereo, 2, 0.5, 0.5)
        out, self._state = audioop.ratecv(mono, 2, 1, 48000, 16000, self._state)
        return out
