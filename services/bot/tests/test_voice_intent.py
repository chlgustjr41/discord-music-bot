"""Voice grammar: ordered matching, with song search as the only free-form case."""

import pytest

from jacky.api.voice_actions import Action
from jacky.api.voice_intent import normalize_playlist_name, parse_fallback


@pytest.mark.parametrize(
    ("a", "b"),
    [("Chill Vibes", "chill vibes"), ("Late-Night!", "latenight"), ("A B", "ab")],
)
def test_playlist_name_normalization_matches_loosely(a, b):
    assert normalize_playlist_name(a) == normalize_playlist_name(b)


# ── transcription client ─────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status, payload):
        self.status, self._payload = status, payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeHttp:
    def __init__(self, response):
        self._response, self.calls = response, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


async def test_transcriber_posts_audio_and_returns_text():
    from jacky.api.transcribe import OpenAITranscriber

    http = _FakeHttp(_FakeResponse(200, {"text": "  play bohemian rhapsody "}))
    t = OpenAITranscriber(http, "sk-test", "gpt-4o-mini-transcribe")
    assert await t.transcribe(b"RIFFfake") == "play bohemian rhapsody"

    url, kwargs = http.calls[0]
    assert url.endswith("/audio/transcriptions")
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"


async def test_transcriber_raises_on_non_200():
    from jacky.api.transcribe import OpenAITranscriber, TranscribeError

    t = OpenAITranscriber(_FakeHttp(_FakeResponse(500, {})), "sk", "m")
    with pytest.raises(TranscribeError):
        await t.transcribe(b"RIFFfake")


async def test_transcriber_wraps_network_faults():
    from jacky.api.transcribe import OpenAITranscriber, TranscribeError

    class _Boom:
        def post(self, *a, **k):
            raise OSError("connection reset")

    t = OpenAITranscriber(_Boom(), "sk", "m")
    with pytest.raises(TranscribeError) as exc:
        await t.transcribe(b"RIFFfake")
    assert isinstance(exc.value.__cause__, OSError)


async def test_transcriber_does_not_double_wrap_a_status_error():
    """The non-200 TranscribeError must propagate as-is, not get re-wrapped
    by the broad network handler — otherwise __cause__ would be a
    TranscribeError and the message would nest."""
    from jacky.api.transcribe import OpenAITranscriber, TranscribeError

    t = OpenAITranscriber(_FakeHttp(_FakeResponse(500, {})), "sk", "m")
    with pytest.raises(TranscribeError) as exc:
        await t.transcribe(b"RIFFfake")
    assert exc.value.__cause__ is None
    assert "500" in str(exc.value)


# ── fallback parser (LLM action vocabulary) ──────────────────────────────


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
        ("clear the queue", Action("clear_queue")),
        # Punctuation normalizes to a space, so "what's playing" and the
        # apostrophe-less spelling are two DIFFERENT keys. Both are exercised.
        ("what's playing", Action("now_playing")),
        ("whats playing", Action("now_playing")),
        ("what is playing", Action("now_playing")),
        ("now playing", Action("now_playing")),
        ("post the session", Action("session_info")),
        ("post the session code", Action("session_info")),
        ("session code", Action("session_info")),
        ("open the dashboard", Action("open_dashboard")),
        ("open dashboard", Action("open_dashboard")),
    ],
)
def test_fallback_exact_commands(said, action):
    assert parse_fallback(said) == [action]


@pytest.mark.parametrize(
    ("said", "query"),
    [
        # The exact table matches a WHOLE normalized transcript, so a song
        # whose title collides with a command word is still a search. Pinned
        # so a future move to prefix matching cannot silently eat searches.
        ("play now playing by the band", "now playing by the band"),
        # The load-bearing cases: a verbless search that STARTS with a
        # command phrase. Prefix matching would swallow these; exact
        # whole-transcript matching lets them fall through to search.
        ("now playing by the band", "now playing by the band"),
        ("open dashboard confessional", "open dashboard confessional"),
        ("play open the dashboard", "open the dashboard"),
        ("queue session code", "session code"),
    ],
)
def test_fallback_command_words_inside_a_song_search_still_search(said, query):
    assert parse_fallback(said)[0].action == "play"
    assert parse_fallback(said)[0].query == query


@pytest.mark.parametrize(
    ("said", "query", "placement"),
    [
        ("play bohemian rhapsody", "bohemian rhapsody", "now"),
        ("play bohemian rhapsody next", "bohemian rhapsody", "next"),
        ("add bohemian rhapsody", "bohemian rhapsody", "end"),
        ("queue bohemian rhapsody", "bohemian rhapsody", "end"),
        ("bohemian rhapsody", "bohemian rhapsody", "now"),
        # Trailing sentence punctuation is stripped from the query.
        ("play bohemian rhapsody.", "bohemian rhapsody", "now"),
        # "playlist" only counts directly after the verb.
        ("play the playlist song", "the playlist song", "now"),
        # A bare verb is not a command, and not a search for the empty string.
        ("play", "play", "now"),
    ],
)
def test_fallback_play_placements(said, query, placement):
    assert parse_fallback(said) == [
        Action("play", query=query, placement=placement)
    ]


def test_fallback_playlist_placements():
    assert parse_fallback("play playlist chill") == [
        Action("playlist", name="chill", placement="now")
    ]
    assert parse_fallback("add playlist chill") == [
        Action("playlist", name="chill", placement="end")
    ]
    assert parse_fallback("queue playlist chill") == [
        Action("playlist", name="chill", placement="end")
    ]
    # Matching normalizes case; the NAME keeps the case it was spoken in.
    assert parse_fallback("Play playlist Chill Vibes") == [
        Action("playlist", name="Chill Vibes", placement="now")
    ]


def test_fallback_empty_transcript_is_empty_list():
    assert parse_fallback("") == []
    assert parse_fallback("   ") == []
    assert parse_fallback("...") == []


def test_fallback_query_keeps_original_punctuation_and_case():
    assert parse_fallback("play AC/DC Back in Black") == [
        Action("play", query="AC/DC Back in Black", placement="now")
    ]


def test_fallback_incomplete_playlist_command_is_not_a_search_for_playlist():
    """'play playlist' with no name must not become a song search for the
    literal word 'playlist' — the existing grammar's guard, preserved."""
    assert parse_fallback("play playlist") == [
        Action("play", query="play playlist", placement="now")
    ]
