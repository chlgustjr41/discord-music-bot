"""Lavalink frame statistics: parsing, classification, rendering, logging.

Listeners hear the music speed up and slow down when frames do not go out on
an even 20 ms beat. Lavalink counts two different reasons for that — the
source had nothing ready (`nulled`) or the sender did not send on time
(`deficit`) — and the bot used to throw the count away.
"""

import logging

import pytest

from jacky.audio.stats import (
    EXPECTED_FRAMES,
    LOSS_THRESHOLD,
    AudioStats,
    classify_frame_loss,
    describe_audio,
)


def sample(sent=3000, nulled=0, deficit=0, playing=1, lavalink=0.04, system=0.12,
           received_at=100.0):
    return AudioStats(
        sent=sent, nulled=nulled, deficit=deficit, playing_players=playing,
        lavalink_load=lavalink, system_load=system, received_at=received_at,
    )


STATS_OP = {
    "op": "stats",
    "players": 2,
    "playingPlayers": 1,
    "uptime": 123456,
    "memory": {"free": 1, "used": 2, "allocated": 3, "reservable": 4},
    "cpu": {"cores": 4, "systemLoad": 0.12, "lavalinkLoad": 0.04},
    "frameStats": {"sent": 2877, "nulled": 120, "deficit": 3},
}


# ── classification ───────────────────────────────────────────────────────

def test_the_constants_are_the_documented_ones():
    assert EXPECTED_FRAMES == 3000      # one 20 ms frame per player-minute
    assert LOSS_THRESHOLD == 0.02


def test_no_sample_is_not_a_loss():
    assert classify_frame_loss(None) is None


def test_a_healthy_sample_is_not_a_loss():
    assert classify_frame_loss(sample()) is None


def test_a_sample_without_frame_stats_is_not_a_loss():
    """Lavalink omits frameStats until a stream has played a full minute."""
    assert classify_frame_loss(sample(sent=None, nulled=None, deficit=None)) is None


def test_nothing_playing_is_not_a_loss_whatever_the_numbers_say():
    assert classify_frame_loss(sample(nulled=500, deficit=500, playing=0)) is None


@pytest.mark.parametrize("nulled,deficit", [(59, 0), (0, 59), (30, 29), (0, 0)])
def test_below_two_percent_is_not_a_loss(nulled, deficit):
    assert classify_frame_loss(sample(nulled=nulled, deficit=deficit)) is None


@pytest.mark.parametrize("nulled,deficit,cause", [
    (60, 0, "source"), (0, 60, "sender"), (31, 29, "source"), (29, 31, "sender"),
])
def test_exactly_two_percent_is_a_loss(nulled, deficit, cause):
    loss = classify_frame_loss(sample(nulled=nulled, deficit=deficit))
    assert loss is not None
    assert loss.cause == cause
    assert loss.ratio == pytest.approx(0.02)


def test_nulled_dominating_is_the_source():
    loss = classify_frame_loss(sample(sent=2877, nulled=120, deficit=3))
    assert loss.cause == "source" and loss.label == "source starvation"
    assert loss.ratio == pytest.approx(123 / 3000)


def test_deficit_dominating_is_the_sender():
    loss = classify_frame_loss(sample(sent=2700, nulled=10, deficit=290))
    assert loss.cause == "sender" and loss.label == "sender stall"
    assert loss.ratio == pytest.approx(300 / 3000)


def test_an_exact_tie_is_reported_as_the_source():
    """Arbitrary but pinned: the source is the side with independent
    evidence of trouble (mid-play track failures), so it is the first place
    to look when the counts cannot say."""
    assert classify_frame_loss(sample(nulled=100, deficit=100)).cause == "source"


def test_a_negative_deficit_counts_as_zero():
    """Negative means MORE frames went out than expected — not a loss, and
    it must not cancel out nulled frames either."""
    assert classify_frame_loss(sample(nulled=10, deficit=-500)) is None
    loss = classify_frame_loss(sample(nulled=100, deficit=-500))
    assert loss.cause == "source"
    assert loss.ratio == pytest.approx(100 / 3000)
    assert classify_frame_loss(sample(nulled=0, deficit=-3000)) is None


def test_the_expected_count_does_not_scale_with_the_number_of_players():
    """Lavalink reports frameStats as a per-player AVERAGE, so a sample is
    always measured against one player's 3000 frames. Scaling by
    playingPlayers would hide a 4% loss as 1% the moment four guilds play."""
    loss = classify_frame_loss(sample(nulled=120, playing=4))
    assert loss is not None and loss.ratio == pytest.approx(0.04)


# ── parsing the stats op ─────────────────────────────────────────────────

def test_from_payload_keeps_only_the_fields_that_matter():
    stats = AudioStats.from_payload(STATS_OP, received_at=42.0)
    assert stats == AudioStats(
        sent=2877, nulled=120, deficit=3, playing_players=1,
        lavalink_load=0.04, system_load=0.12, received_at=42.0,
    )


def test_from_payload_without_frame_stats():
    idle = {**STATS_OP, "playingPlayers": 0, "frameStats": None}
    stats = AudioStats.from_payload(idle, received_at=1.0)
    assert (stats.sent, stats.nulled, stats.deficit) == (None, None, None)
    assert stats.playing_players == 0 and stats.lavalink_load == 0.04
    assert not stats.has_frames

    absent = {k: v for k, v in STATS_OP.items() if k != "frameStats"}
    assert not AudioStats.from_payload(absent, received_at=1.0).has_frames


@pytest.mark.parametrize("payload", [
    {},
    {"op": "stats"},
    {"frameStats": "lots", "cpu": "busy", "playingPlayers": "one"},
    {"frameStats": {"sent": "x", "nulled": None, "deficit": []}},
    {"frameStats": {"sent": 3000}},                       # partial
    {"frameStats": {"sent": True, "nulled": 0, "deficit": 0}},
    {"cpu": {"lavalinkLoad": float("nan"), "systemLoad": float("inf")}},
    {"playingPlayers": -3},
])
def test_from_payload_never_raises_and_never_half_fills_the_frames(payload):
    stats = AudioStats.from_payload(payload, received_at=0.0)
    assert not stats.has_frames
    assert (stats.sent, stats.nulled, stats.deficit) == (None, None, None)
    assert stats.playing_players >= 0
    # NaN/inf are not valid JSON; they must not reach /health.
    assert stats.lavalink_load is None or 0 <= stats.lavalink_load <= 1e6
    assert classify_frame_loss(stats) is None


def test_as_health_is_the_documented_shape():
    stats = AudioStats.from_payload(STATS_OP, received_at=100.0)
    assert stats.as_health(now=112.34) == {
        "sent": 2877,
        "nulled": 120,
        "deficit": 3,
        "playingPlayers": 1,
        "lavalinkLoad": 0.04,
        "systemLoad": 0.12,
        "ageSeconds": 12.3,
    }
    # A clock that somehow ran backwards must not report a negative age.
    assert stats.as_health(now=50.0)["ageSeconds"] == 0.0


# ── the j!status line ────────────────────────────────────────────────────

def test_describe_with_no_sample_says_so():
    assert describe_audio(None, None) == "Audio frames: no data yet"


def test_describe_a_healthy_sample():
    line = describe_audio(sample(sent=2998, nulled=0, deficit=2), 3.0)
    assert line == (
        "Audio frames: 2998/3000 sent · 0 nulled · 2 deficit"
        " · Lavalink CPU 4%, host 12%"
    )
    assert "\n" not in line


def test_describe_a_lossy_sample_leads_with_the_classification():
    line = describe_audio(sample(sent=2877, nulled=120, deficit=3), 3.0)
    assert line.startswith("⚠️ Audio frame loss 4.1% (source starvation): ")
    assert "2877/3000 sent · 120 nulled · 3 deficit" in line
    assert "\n" not in line


def test_describe_a_sample_without_frames():
    line = describe_audio(sample(sent=None, nulled=None, deficit=None, playing=0), 3.0)
    assert line == (
        "Audio frames: no stream measured for a full minute yet"
        " · Lavalink CPU 4%, host 12%"
    )


def test_describe_marks_a_stale_sample():
    """Lavalink sends stats about once a minute; a sample several minutes old
    describes a node that has since stopped talking."""
    fresh = describe_audio(sample(), 119.0)
    stale = describe_audio(sample(), 600.0)
    assert "old" not in fresh
    assert stale.endswith(" · 10m old")


def test_describe_omits_cpu_when_lavalink_did_not_report_it():
    line = describe_audio(sample(lavalink=None, system=None), 1.0)
    assert line == "Audio frames: 3000/3000 sent · 0 nulled · 0 deficit"


# ── PlayerService.on_stats: cache + one WARNING per affected sample ──────

async def test_on_stats_keeps_the_latest_sample(service):
    assert service.audio_stats is None
    first, second = sample(received_at=1.0), sample(sent=2990, received_at=2.0)
    await service.on_stats(first)
    await service.on_stats(second)
    assert service.audio_stats is second


async def test_on_stats_warns_once_per_lossy_sample_with_counts_only(service, caplog):
    with caplog.at_level(logging.DEBUG, logger="jacky.player"):
        await service.on_stats(sample(sent=2877, nulled=120, deficit=3))
    [record] = [r for r in caplog.records if r.name == "jacky.player"]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == (
        "audio frame loss 4.1% (source starvation): "
        "sent=2877 nulled=120 deficit=3 playing=1"
    )


async def test_on_stats_names_the_sender_when_deficit_dominates(service, caplog):
    with caplog.at_level(logging.DEBUG, logger="jacky.player"):
        await service.on_stats(sample(sent=2700, nulled=10, deficit=290, playing=2))
    [record] = [r for r in caplog.records if r.name == "jacky.player"]
    assert record.getMessage() == (
        "audio frame loss 10.0% (sender stall): "
        "sent=2700 nulled=10 deficit=290 playing=2"
    )


async def test_on_stats_is_silent_for_healthy_and_idle_samples(service, caplog):
    with caplog.at_level(logging.DEBUG, logger="jacky.player"):
        await service.on_stats(sample())
        await service.on_stats(sample(nulled=59))
        await service.on_stats(sample(sent=None, nulled=None, deficit=None, playing=0))
    assert [r for r in caplog.records if r.name == "jacky.player"] == []


async def test_the_frame_loss_warning_carries_no_guild_or_track_information(
    service, guild_id, sid, caplog
):
    await service.repo.set_current_track(sid, {"title": "Secret Song Title"})
    with caplog.at_level(logging.DEBUG):
        await service.on_stats(sample(nulled=300))
    # Messages, not caplog.text: the formatted text also carries source line
    # numbers, which can coincide with a short numeric guild id.
    logged = " | ".join(r.getMessage() for r in caplog.records)
    assert "frame loss" in logged
    assert "Secret Song Title" not in logged
    assert str(guild_id) not in logged and "Guild" not in logged
