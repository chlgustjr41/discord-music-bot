"""Voice grammar: ordered matching, with song search as the only free-form case."""

import pytest

from jacky.api.voice_intent import Intent, normalize_playlist_name, parse_intent


@pytest.mark.parametrize(
    ("said", "kind"),
    [
        ("skip", "skip"),
        ("Skip.", "skip"),
        ("next", "skip"),
        ("skip track", "skip"),
        ("pause", "pause"),
        ("resume", "resume"),
        ("unpause", "resume"),
        ("continue", "resume"),
        ("volume up", "volume_up"),
        ("louder", "volume_up"),
        ("turn it up", "volume_up"),
        ("volume down", "volume_down"),
        ("quieter", "volume_down"),
        ("turn it down", "volume_down"),
    ],
)
def test_exact_commands(said, kind):
    assert parse_intent(said) == Intent(kind, "")


@pytest.mark.parametrize(
    ("said", "kind", "arg"),
    [
        ("play playlist chill vibes", "playlist_play", "chill vibes"),
        ("Play playlist Chill Vibes", "playlist_play", "Chill Vibes"),
        ("add playlist chill", "playlist_add", "chill"),
        ("queue playlist chill", "playlist_add", "chill"),
    ],
)
def test_playlist_commands(said, kind, arg):
    assert parse_intent(said) == Intent(kind, arg)


@pytest.mark.parametrize(
    ("said", "arg"),
    [
        ("play bohemian rhapsody", "bohemian rhapsody"),
        ("add bohemian rhapsody", "bohemian rhapsody"),
        ("queue bohemian rhapsody", "bohemian rhapsody"),
        # The free-form case: no verb at all.
        ("bohemian rhapsody", "bohemian rhapsody"),
        # "playlist" only counts directly after the verb.
        ("play the playlist song", "the playlist song"),
    ],
)
def test_search(said, arg):
    assert parse_intent(said) == Intent("search", arg)


def test_query_keeps_original_punctuation_and_case():
    """Matching normalizes; the ARGUMENT must not — 'AC/DC' is a real band."""
    assert parse_intent("play AC/DC Back in Black") == Intent(
        "search", "AC/DC Back in Black"
    )


def test_trailing_sentence_punctuation_is_stripped_from_queries():
    assert parse_intent("play bohemian rhapsody.") == Intent(
        "search", "bohemian rhapsody"
    )


def test_empty_transcript_is_none():
    assert parse_intent("") is None
    assert parse_intent("   ") is None
    assert parse_intent("...") is None


def test_bare_verb_is_not_a_command():
    """'play' with nothing after it is not a search for the empty string."""
    assert parse_intent("play") == Intent("search", "play")
    assert parse_intent("play playlist") == Intent("search", "play playlist")


def test_stop_is_not_a_voice_command():
    """Excluded by design: one misrecognition would clear the queue."""
    assert parse_intent("stop") == Intent("search", "stop")


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
