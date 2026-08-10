"""OpenAITranscriber: the speech-to-text client and its failure modes."""

import pytest


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


def _fields(form) -> dict:
    """aiohttp keeps FormData parts as (type_options, headers, value)."""
    return {opts["name"]: value for opts, _headers, value in form._fields}


async def test_transcriber_posts_audio_and_returns_text():
    from jacky.api.transcribe import OpenAITranscriber

    http = _FakeHttp(_FakeResponse(200, {"text": "  play bohemian rhapsody "}))
    t = OpenAITranscriber(http, "sk-test", "gpt-4o-mini-transcribe")
    assert await t.transcribe(b"RIFFfake") == "play bohemian rhapsody"

    url, kwargs = http.calls[0]
    assert url.endswith("/audio/transcriptions")
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"


async def test_transcriber_sends_the_language_field():
    """Fixing the language beats autodetect on clips this short — that is the
    entire reason the setting exists, so the field must actually be sent."""
    from jacky.api.transcribe import OpenAITranscriber

    http = _FakeHttp(_FakeResponse(200, {"text": "다음 곡"}))
    await OpenAITranscriber(http, "sk", "m").transcribe(b"RIFFfake", "ko")
    assert _fields(http.calls[0][1]["data"])["language"] == "ko"


async def test_transcriber_defaults_to_english_and_normalizes_junk():
    """The transcriber is injected, so it cannot assume the route already
    normalized: an unknown code degrades here too rather than reaching OpenAI."""
    from jacky.api.transcribe import OpenAITranscriber

    http = _FakeHttp(_FakeResponse(200, {"text": "hi"}))
    t = OpenAITranscriber(http, "sk", "m")
    await t.transcribe(b"RIFFfake")
    assert _fields(http.calls[0][1]["data"])["language"] == "en"
    await t.transcribe(b"RIFFfake", "klingon")
    assert _fields(http.calls[1][1]["data"])["language"] == "en"


def test_normalize_language_allowlist():
    """One pure helper owns the allowlist, so the plugin's dropdown and the
    server cannot drift apart on what counts as valid."""
    from jacky.api.transcribe import LANGUAGES, normalize_language

    assert set(LANGUAGES) == {"en", "ko", "ja", "es", "fr", "de", "zh"}
    for code in LANGUAGES:
        assert normalize_language(code) == code
    assert normalize_language("KO") == "ko"
    assert normalize_language(" ja ") == "ja"
    # A bad key setting degrades to English rather than erroring.
    assert normalize_language(None) == "en"
    assert normalize_language("") == "en"
    assert normalize_language("klingon") == "en"
    # The allowlist is exact: a locale is not a language code here.
    assert normalize_language("ko-KR") == "en"


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
