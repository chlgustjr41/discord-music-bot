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


class FakeEarsClient:
    """Stands in for EarsClient: only the attributes EarsSink touches."""

    def __init__(self):
        self.dispatched: list = []

    def dispatch_event(self, guild_id, event):
        self.dispatched.append((guild_id, event))


class FakeVoiceData:
    def __init__(self, opus: bytes):
        self.opus = opus


class FakeUser:
    def __init__(self, uid: int, bot: bool = False):
        self.id, self.bot = uid, bot


def _loud_pcm() -> bytes:
    # 20 ms of full-amplitude 48k stereo s16le — well above the silence gate.
    return struct.pack("<hh", 20000, 20000) * 960


class FakeDecoder:
    def __init__(self, pcm: bytes | None = None, raises: bool = False):
        self._pcm, self._raises = pcm or _loud_pcm(), raises

    def decode(self, opus, *, fec=False):
        if self._raises:
            # __new__ bypasses OpusError.__init__, which needs libopus loaded
            # (absent in the local test env); the `except OpusError` in write()
            # still matches by type. Real decodes on the VM raise it normally.
            raise OpusError.__new__(OpusError)
        return self._pcm


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


def test_wants_opus_true():
    """Must be True so voice_recv doesn't decode in its fatal loop."""
    assert EarsSink(FakeEarsClient(), "1", "hey jacky").wants_opus() is True


def test_sink_skips_bots_and_empty_packets():
    client = FakeEarsClient()
    sink = EarsSink(client, "1", "hey jacky")
    sink.streams[99] = (FakeDecoder(), FakeDS(), FakeEngine())
    sink.write(FakeUser(99, bot=True), FakeVoiceData(b"\xfe\xff"))   # bot
    sink.write(FakeUser(99), FakeVoiceData(b""))                     # empty
    assert client.dispatched == []


def test_corrupt_packet_is_skipped_not_fatal():
    """A decode error must NOT propagate (that killed the whole loop in prod)."""
    client = FakeEarsClient()
    sink = EarsSink(client, "1", "hey jacky")
    sink.streams[42] = (FakeDecoder(raises=True), FakeDS(), FakeEngine())
    sink.write(FakeUser(42), FakeVoiceData(b"\x01\x02\x03"))   # must not raise
    assert client.dispatched == []


def test_sink_write_dispatches_engine_events():
    """A recognized event from a speaker's engine reaches the client."""
    client = FakeEarsClient()
    sink = EarsSink(client, "7", "hey jacky")
    sink.streams[42] = (FakeDecoder(), FakeDS(), FakeEngine(("wake", None)))
    sink.write(FakeUser(42), FakeVoiceData(b"\x01\x02\x03"))
    assert client.dispatched == [("7", ("wake", None))]


def test_cleanup_is_safe_before_full_init():
    """AudioSink.__del__ calls cleanup(); it must never raise even if streams
    was never set (construction failed partway)."""
    sink = EarsSink.__new__(EarsSink)   # skip __init__
    sink.cleanup()                       # must not raise
