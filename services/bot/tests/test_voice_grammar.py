"""The deterministic grammar: it resolves the closed vocabulary, and when it
cannot, it resolves NOTHING. There is no free-form search branch."""

import pytest

from jacky.api.voice_actions import Action
from jacky.api.voice_grammar import normalize_playlist_name, parse_structured


@pytest.mark.parametrize(
    ("a", "b"),
    [("Chill Vibes", "chill vibes"), ("Late-Night!", "latenight"), ("A B", "ab")],
)
def test_playlist_name_normalization_matches_loosely(a, b):
    assert normalize_playlist_name(a) == normalize_playlist_name(b)


# ── media control ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("said", "action"),
    [
        ("skip", Action("skip", count=1)),
        ("Skip.", Action("skip", count=1)),
        ("next", Action("skip", count=1)),
        ("skip track", Action("skip", count=1)),
        ("pause", Action("pause")),
        ("stop", Action("pause")),          # stop-like speech pauses
        ("resume", Action("resume")),
        ("unpause", Action("resume")),
        ("continue", Action("resume")),
        ("louder", Action("volume", delta=10)),
        ("volume up", Action("volume", delta=10)),
        ("turn it up", Action("volume", delta=10)),
        ("quieter", Action("volume", delta=-10)),
        ("volume down", Action("volume", delta=-10)),
        ("turn it down", Action("volume", delta=-10)),
        ("shuffle", Action("shuffle")),
        ("shuffle the queue", Action("shuffle")),
        ("clear", Action("clear_queue")),
        ("clear queue", Action("clear_queue")),
        ("clear the queue", Action("clear_queue")),
        # Punctuation normalizes to a SPACE, so "what's playing" and the
        # apostrophe-less spelling are two DIFFERENT keys. Both are exercised.
        ("what's playing", Action("now_playing")),
        ("whats playing", Action("now_playing")),
        ("what is playing", Action("now_playing")),
        ("now playing", Action("now_playing")),
        ("post the session", Action("session_info")),
        ("post the session code", Action("session_info")),
        ("post session code", Action("session_info")),
        ("session code", Action("session_info")),
        ("open the dashboard", Action("open_dashboard")),
        ("open dashboard", Action("open_dashboard")),
    ],
)
def test_exact_commands(said, action):
    assert parse_structured(said).actions == [action]
    assert parse_structured(said).resolved is True


@pytest.mark.parametrize(
    ("said", "mode"),
    [
        ("repeat", "track"),
        ("repeat this song", "track"),
        ("loop", "track"),
        ("loop track", "track"),
        ("loop queue", "queue"),
        ("loop off", "off"),
        ("repeat off", "off"),
    ],
)
def test_loop_modes(said, mode):
    assert parse_structured(said).actions == [Action("loop", mode=mode)]


@pytest.mark.parametrize(
    ("said", "count"),
    [
        ("skip 2", 2), ("skip two", 2), ("skip 3 songs", 3),
        ("next 2", 2), ("skip ten", 10),
    ],
)
def test_skip_counts(said, count):
    assert parse_structured(said).actions == [Action("skip", count=count)]


# ── the overloaded word "next" ───────────────────────────────────────────


@pytest.mark.parametrize("said", ["next", "next song", "next track"])
def test_leading_next_is_media_control_never_a_search(said):
    """The regression in the title: "next song" used to become a SEARCH for
    the word "song", which also interrupted whatever was playing."""
    assert parse_structured(said).actions == [Action("skip", count=1)]


def test_trailing_next_is_placement():
    assert parse_structured("play bohemian rhapsody next").actions == [
        Action("play", query="bohemian rhapsody", placement="next")
    ]


def test_play_next_song_is_a_title_not_a_placement():
    """Deliberate choice, see the comment in voice_grammar: "next" here
    neither leads the transcript nor trails it, so it is part of the title."""
    assert parse_structured("play next song").actions == [
        Action("play", query="next song", placement="now")
    ]


@pytest.mark.parametrize("said", ["play next", "add next", "queue next"])
def test_a_verb_plus_a_bare_next_is_never_a_search_for_next(said):
    """A verb with "next" as its ENTIRE argument has no argument.

    "play next" almost certainly means skip, but the grammar does not guess:
    resolving it would search YouTube for the literal string "next" and (for
    "play") replace the current track, and the reasoning layer would never
    see it. Unresolved, so reasoning gets a chance.
    """
    parsed = parse_structured(said)
    assert parsed.resolved is False
    assert parsed.actions == []


@pytest.mark.parametrize("said", ["next next", "skip next"])
def test_a_doubled_next_is_unresolved_not_a_search(said):
    """"skip" and "next" are not play verbs, so these never reached the
    prefix branch: _SKIP declines them (its optional noun is only
    "song"/"track") and they were already unresolved. Pinned so the new
    guard is not credited with behaviour it does not provide."""
    parsed = parse_structured(said)
    assert parsed.resolved is False
    assert parsed.actions == []


# ── volume ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("said", ["volume 50", "volume to 50", "set volume 50"])
def test_volume_absolute(said):
    assert parse_structured(said).actions == [Action("volume", level=50)]


@pytest.mark.parametrize(
    ("said", "delta"), [("volume up", 10), ("louder", 10), ("quieter", -10)]
)
def test_volume_relative(said, delta):
    got = parse_structured(said).actions[0]
    assert got == Action("volume", delta=delta)
    assert got.level is None      # relative, not absolute


# ── placements ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("said", "query", "placement"),
    [
        ("play bohemian rhapsody", "bohemian rhapsody", "now"),
        ("play bohemian rhapsody next", "bohemian rhapsody", "next"),
        ("add bohemian rhapsody", "bohemian rhapsody", "end"),
        ("queue bohemian rhapsody", "bohemian rhapsody", "end"),
        ("play bohemian rhapsody.", "bohemian rhapsody", "now"),
    ],
)
def test_play_placements(said, query, placement):
    assert parse_structured(said).actions == [
        Action("play", query=query, placement=placement)
    ]


@pytest.mark.parametrize(
    ("said", "name", "placement"),
    [
        ("play playlist chill", "chill", "now"),
        ("play playlist chill next", "chill", "next"),
        ("add playlist chill", "chill", "end"),
        ("queue playlist chill", "chill", "end"),
    ],
)
def test_playlist_placements(said, name, placement):
    assert parse_structured(said).actions == [
        Action("playlist", name=name, placement=placement)
    ]


def test_argument_is_sliced_from_the_original_text():
    """The ARGUMENT comes from the original transcript, never the normalized
    one, so punctuation and case survive."""
    assert parse_structured("play AC/DC Back in Black").actions == [
        Action("play", query="AC/DC Back in Black", placement="now")
    ]
    assert parse_structured("Play playlist Chill Vibes").actions == [
        Action("playlist", name="Chill Vibes", placement="now")
    ]


# ── the unresolved case: the whole point of the task ─────────────────────


@pytest.mark.parametrize(
    "said",
    [
        # THE regression. Every one of these used to become a YouTube search
        # with placement "now", i.e. a mumble could interrupt the music. The
        # catch-all is deleted: no verb, no action.
        "bohemian rhapsody",
        "uh what was that",
        "now playing by the band",
        "open dashboard confessional",
        "",
        "   ",
        "...",
        "play",                     # bare verb, no argument
        "play playlist",            # no name — must not search for "playlist"
    ],
)
def test_unrecognised_phrases_resolve_to_nothing(said):
    got = parse_structured(said)
    assert got.actions == []
    assert got.resolved is False


def test_keywords_report_the_vocabulary_that_was_present():
    """Not used here — the reasoning layer consumes them as hints."""
    assert "playlist" in parse_structured("uh the playlist thing").keywords
    assert "next" in parse_structured("next song").keywords
    assert parse_structured("bohemian rhapsody").keywords == []
