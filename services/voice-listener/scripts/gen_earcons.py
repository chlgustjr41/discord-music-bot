"""Generate earcon WAVs (48k stereo s16le, FFmpeg-friendly). Run from
services/voice-listener: python scripts/gen_earcons.py"""

import math
import struct
import wave
from pathlib import Path

RATE = 48000

def tone(freqs: list[tuple[float, float]], amp: float = 0.35) -> bytes:
    out = bytearray()
    for freq, dur in freqs:
        n = int(RATE * dur)
        for i in range(n):
            env = min(1.0, i / 480, (n - i) / 480)          # 10ms fade in/out
            v = int(32767 * amp * env * math.sin(2 * math.pi * freq * i / RATE))
            out += struct.pack("<hh", v, v)
    return bytes(out)

EARCONS = {
    # Wake confirmation ("I heard you — now listening"): a bright rising
    # two-tone ring (G5 · gap · C6 · E6) so it clearly stands out over music
    # and reads as an "active listening" cue. freq=0 renders as a short gap.
    "ack.wav": [(784, 0.10), (0, 0.04), (1047, 0.12), (1319, 0.16)],
    "confirm.wav": [(880, 0.08)],                # blip: "command done"
    "error.wav": [(220, 0.18)],                  # low buzz: "didn't get that"
}

assets = Path(__file__).resolve().parent.parent / "assets"
assets.mkdir(exist_ok=True)
for name, spec in EARCONS.items():
    with wave.open(str(assets / name), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(tone(spec))
    print("wrote", assets / name)
