import math
import struct

from ears.pipeline import Downsampler, is_silence


def sine_48k_stereo(ms: int, freq: int = 440, amp: int = 12000) -> bytes:
    frames = 48 * ms
    out = bytearray()
    for i in range(frames):
        v = int(amp * math.sin(2 * math.pi * freq * i / 48000))
        out += struct.pack("<hh", v, v)
    return bytes(out)


def test_downsample_ratio():
    ds = Downsampler()
    out = ds.feed(sine_48k_stereo(20))            # one Discord frame
    # 48k stereo 16-bit -> 16k mono 16-bit: byte count shrinks 6x
    assert len(out) == len(sine_48k_stereo(20)) // 6


def test_silence_gate():
    assert is_silence(b"\x00\x00" * 960)
    assert not is_silence(sine_48k_stereo(20))
