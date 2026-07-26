"""Regression tests for the audio sink wiring.

gateway.py is thin Discord I/O, but EarsSink's construction and per-speaker
fan-out are pure enough to test without a live voice connection — and MUST be,
because the base voice_recv.AudioSink defines `client` as a read-only property:
assigning self.client in __init__ raised AttributeError and silently broke the
whole feature in production (the sink never attached, so no audio was processed).
"""

import struct

from ears.gateway import EarsSink


class FakeEarsClient:
    """Stands in for EarsClient: only the attributes EarsSink touches."""

    def __init__(self):
        self.dispatched: list = []

    def dispatch_event(self, guild_id, event):
        self.dispatched.append((guild_id, event))


class FakeVoiceData:
    def __init__(self, pcm: bytes):
        self.pcm = pcm


class FakeUser:
    def __init__(self, uid: int, bot: bool = False):
        self.id, self.bot = uid, bot


def _loud_frame() -> bytes:
    # 20 ms of full-amplitude 48k stereo s16le — well above the silence gate.
    return struct.pack("<hh", 20000, 20000) * 960


def test_sink_constructs_without_client_property_collision():
    """The bug: assigning self.client shadowed AudioSink's read-only property."""
    sink = EarsSink(FakeEarsClient(), "1", "hey jacky")
    assert sink.engines == {}
    assert sink.wake_phrase == "hey jacky"
    assert sink.guild_id == "1"


def test_sink_skips_silence_and_bots():
    client = FakeEarsClient()
    sink = EarsSink(client, "1", "hey jacky")
    sink.write(FakeUser(42, bot=True), FakeVoiceData(_loud_frame()))   # bot
    sink.write(FakeUser(43), FakeVoiceData(b"\x00\x00" * 1920))        # silence
    assert client.dispatched == []


def test_sink_write_dispatches_engine_events():
    """A recognized event from a speaker's engine reaches the client."""
    client = FakeEarsClient()
    sink = EarsSink(client, "7", "hey jacky")

    class FakeDS:
        def feed(self, pcm):
            return pcm

    class FakeEngine:
        def feed(self, pcm):
            return ("wake", None)

    sink.engines[42] = (FakeDS(), FakeEngine())   # inject: no Vosk model needed
    sink.write(FakeUser(42), FakeVoiceData(_loud_frame()))
    assert client.dispatched == [("7", ("wake", None))]


def test_cleanup_is_safe_before_full_init():
    """AudioSink.__del__ calls cleanup(); it must never raise even if engines
    was never set (construction failed partway)."""
    sink = EarsSink.__new__(EarsSink)   # skip __init__
    sink.cleanup()                       # must not raise
