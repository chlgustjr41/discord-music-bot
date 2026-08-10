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
