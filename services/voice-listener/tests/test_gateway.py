"""Regression tests for the audio sink wiring.

gateway.py is thin Discord I/O, but EarsSink's construction and per-speaker
fan-out are pure enough to test without a live voice connection — and MUST be,
because two production outages hid here:

1. The base voice_recv.AudioSink defines `client` as a read-only property, so
   assigning self.client in __init__ raised AttributeError and the sink never
   attached (no audio processed at all).
2. voice_recv's own opus decode runs in the packet-router loop with no
   per-packet error handling, so one "corrupted stream" OpusError killed all
   listening. We now decode in write() (wants_opus=True) and skip bad packets.
"""

import struct

from discord.opus import OpusError

from ears.gateway import EarsSink


class FakeSettings:
    debug = False
    debug_capture_seconds = 20
    active_window_seconds = 5.0


class FakeEarsClient:
    """Stands in for EarsClient: only the attributes EarsSink touches."""

    def __init__(self):
        self.dispatched: list = []
        self.settings = FakeSettings()

    def dispatch_event(self, guild_id, event):
        self.dispatched.append((guild_id, event))


class FakeVoiceData:
    def __init__(self, pcm: bytes, opus: bytes = b""):
        self.pcm = pcm
        self.opus = opus


class FakeUser:
    def __init__(self, uid: int, bot: bool = False):
        self.id, self.bot = uid, bot


def _loud_pcm() -> bytes:
    # 20 ms of full-amplitude 48k stereo s16le — well above the silence gate.
    return struct.pack("<hh", 20000, 20000) * 960


class FakeDS:
    def feed(self, pcm):
        return pcm


class FakeEngine:
    def __init__(self, event=("wake", None)):
        self.event = event
        self.calls = []

    def feed(self, pcm, silent):
        self.calls.append(silent)
        return self.event


def test_sink_constructs_without_client_property_collision():
    """The bug: assigning self.client shadowed AudioSink's read-only property."""
    sink = EarsSink(FakeEarsClient(), "1", "hey jacky")
    assert sink.streams == {}
    assert sink.wake_phrase == "hey jacky"
    assert sink.guild_id == "1"


def test_wants_opus_false():
    """False so voice_recv does the correct (ordered/FEC/PLC) opus decode."""
    assert EarsSink(FakeEarsClient(), "1", "hey jacky").wants_opus() is False


def test_sink_skips_bots_and_empty_pcm():
    client = FakeEarsClient()
    sink = EarsSink(client, "1", "hey jacky")
    sink.streams[99] = (FakeDS(), FakeEngine())
    sink.write(FakeUser(99, bot=True), FakeVoiceData(_loud_pcm()))   # bot
    sink.write(FakeUser(99), FakeVoiceData(b""))                     # empty pcm
    assert client.dispatched == []


def test_crash_safe_decode_returns_silence_on_opuserror():
    """The wrapper must swallow OpusError (that crash killed all listening)."""
    from ears.gateway import _SILENT_FRAME, _crash_safe_decode

    def raising(self, data, *, fec=False):
        raise OpusError.__new__(OpusError)   # __new__ skips libopus-needing init

    assert _crash_safe_decode(raising)(object(), b"x") == _SILENT_FRAME


def test_crash_safe_decode_passes_through_success():
    from ears.gateway import _crash_safe_decode

    def ok(self, data, *, fec=False):
        return b"pcm-out"

    assert _crash_safe_decode(ok)(object(), b"x") == b"pcm-out"


def test_sink_write_dispatches_engine_events():
    """A recognized event from a speaker's engine reaches the client."""
    client = FakeEarsClient()
    sink = EarsSink(client, "7", "hey jacky")
    sink.streams[42] = (FakeDS(), FakeEngine(("wake", None)))
    sink.write(FakeUser(42), FakeVoiceData(_loud_pcm()))
    assert client.dispatched == [("7", ("wake", None))]


def test_cleanup_is_safe_before_full_init():
    """AudioSink.__del__ calls cleanup(); it must never raise even if streams
    was never set (construction failed partway)."""
    sink = EarsSink.__new__(EarsSink)   # skip __init__
    sink.cleanup()                       # must not raise
