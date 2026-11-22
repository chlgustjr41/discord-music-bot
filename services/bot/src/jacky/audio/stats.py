"""Lavalink frame statistics: the measurement behind "the music sped up".

Lavalink owes Discord one audio frame every 20 ms. When frames go missing the
listener's client stretches and then rushes the audio to cover the gap, which
is heard as slowing down and speeding up. Lavalink counts the two ways a
frame can go missing, and tells them apart:

- `nulled`  — the SOURCE had no audio ready (the stream is arriving slower
              than real time, or died);
- `deficit` — the SENDER did not send on time (the process was stalled or
              starved of CPU or disk).

It reports both about once a minute in the WebSocket `stats` op. This module
turns that op into a sample, a verdict, and a line for j!status.
"""

import math
from dataclasses import dataclass

# 60 s / 20 ms. NOT multiplied by the number of players: Lavalink divides the
# totals by the players it measured before sending them (StatsCollector:
# `totalSent / playerCount`), so every sample is already one player's worth —
# and `playingPlayers` counts players Lavalink has no full minute for yet, so
# it is not even the divisor that was used.
EXPECTED_FRAMES = 3000

# Below this fraction of EXPECTED_FRAMES a sample is ordinary jitter.
LOSS_THRESHOLD = 0.02
# The same boundary in whole frames, so "exactly 2%" is decided by integer
# comparison rather than by a float's idea of 0.02.
_LOSS_THRESHOLD_FRAMES = round(LOSS_THRESHOLD * EXPECTED_FRAMES)

# Lavalink sends stats roughly every 60 s; three missed in a row means the
# sample describes a node that has stopped talking, not the present.
STALE_AFTER_S = 180.0

_LABELS = {"source": "source starvation", "sender": "sender stall"}


def _count(value: object) -> int | None:
    # bool is an int subclass; True is not a frame count.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _load(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # NaN and infinity are not valid JSON, and this value goes to /health.
    return float(value) if math.isfinite(value) else None


@dataclass(frozen=True)
class AudioStats:
    """One `stats` op, reduced to what the frame-loss question needs.

    `sent`/`nulled`/`deficit` are per-player averages over the last full
    minute, and are all None together when Lavalink sent no frameStats —
    which it does until some stream has played an uninterrupted minute.
    `received_at` is time.monotonic() at receipt, for staleness.
    """

    sent: int | None
    nulled: int | None
    deficit: int | None
    playing_players: int
    lavalink_load: float | None
    system_load: float | None
    received_at: float

    @classmethod
    def from_payload(cls, payload: dict, *, received_at: float) -> "AudioStats":
        """Never raises: this runs inside the node's read loop, where an
        exception would drop the Lavalink connection over a telemetry frame."""
        frames = payload.get("frameStats")
        frames = frames if isinstance(frames, dict) else {}
        counts = [_count(frames.get(key)) for key in ("sent", "nulled", "deficit")]
        if None in counts:
            # All or nothing: two real counts beside a missing one would
            # still be fed to the classifier as if they were a measurement.
            counts = [None, None, None]
        cpu = payload.get("cpu")
        cpu = cpu if isinstance(cpu, dict) else {}
        return cls(
            sent=counts[0],
            nulled=counts[1],
            deficit=counts[2],
            playing_players=max(0, _count(payload.get("playingPlayers")) or 0),
            lavalink_load=_load(cpu.get("lavalinkLoad")),
            system_load=_load(cpu.get("systemLoad")),
            received_at=received_at,
        )

    @property
    def has_frames(self) -> bool:
        return self.sent is not None

    def as_health(self, *, now: float) -> dict:
        """The `audio` object of GET /health (the guardian's contract)."""
        return {
            "sent": self.sent,
            "nulled": self.nulled,
            "deficit": self.deficit,
            "playingPlayers": self.playing_players,
            "lavalinkLoad": self.lavalink_load,
            "systemLoad": self.system_load,
            "ageSeconds": round(max(0.0, now - self.received_at), 1),
        }


@dataclass(frozen=True)
class FrameLoss:
    cause: str    # "source" | "sender"
    ratio: float  # lost frames / EXPECTED_FRAMES

    @property
    def label(self) -> str:
        return _LABELS[self.cause]


def classify_frame_loss(stats: AudioStats | None) -> FrameLoss | None:
    """None when there is nothing to report: no sample, nothing playing, no
    frame stats, or loss under LOSS_THRESHOLD. Otherwise which side lost the
    frames, and what fraction of a minute's worth went missing.

    A negative deficit means MORE frames went out than expected. That is not
    loss, and it must not cancel out frames that really were nulled, so it
    counts as zero. An exact tie is called for the source.
    """
    if stats is None or not stats.has_frames or stats.playing_players <= 0:
        return None
    nulled = max(0, stats.nulled)
    deficit = max(0, stats.deficit)
    lost = nulled + deficit
    if lost < _LOSS_THRESHOLD_FRAMES:
        return None
    return FrameLoss(
        cause="source" if nulled >= deficit else "sender",
        ratio=lost / EXPECTED_FRAMES,
    )


def describe_audio(stats: AudioStats | None, age_s: float | None) -> str:
    """The latest sample as ONE line for the j!status embed. Counts and the
    verdict only — a sample carries no guild or track information to leak."""
    if stats is None:
        return "Audio frames: no data yet"
    if stats.has_frames:
        counts = (
            f"{stats.sent}/{EXPECTED_FRAMES} sent · {stats.nulled} nulled"
            f" · {stats.deficit} deficit"
        )
        loss = classify_frame_loss(stats)
        if loss:
            line = f"⚠️ Audio frame loss {loss.ratio:.1%} ({loss.label}): {counts}"
        else:
            line = f"Audio frames: {counts}"
    else:
        line = "Audio frames: no stream measured for a full minute yet"
    loads = [
        f"{name} {load:.0%}"
        for name, load in (("Lavalink CPU", stats.lavalink_load), ("host", stats.system_load))
        if load is not None
    ]
    if loads:
        line += " · " + ", ".join(loads)
    if age_s is not None and age_s >= STALE_AFTER_S:
        line += f" · {int(age_s // 60)}m old"
    return line
