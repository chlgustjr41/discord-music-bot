"""OpenAI speech-to-text. Injectable so tests never touch the network.

Audio is streamed straight through — never written to disk here or anywhere
else in the request path.
"""

from typing import Any

import aiohttp

URL = "https://api.openai.com/v1/audio/transcriptions"

# The languages the Voice key offers. Kept here, next to the only code that
# sends the field, so the plugin's dropdown and the server cannot disagree
# about what is valid — a mismatch would silently transcribe in the wrong
# language, which looks exactly like a bad microphone.
LANGUAGES = ("en", "ko", "ja", "es", "fr", "de", "zh")
DEFAULT_LANGUAGE = "en"


def normalize_language(value: str | None) -> str:
    """Any unknown or missing code becomes English.

    Pure, so it is testable without HTTP. It DEGRADES rather than raising: a
    stale or mistyped key setting must still leave a working key, and the cost
    of guessing wrong is a worse transcription, not a dead button.
    """
    code = (value or "").strip().lower()
    return code if code in LANGUAGES else DEFAULT_LANGUAGE


class TranscribeError(Exception):
    pass


class OpenAITranscriber:
    def __init__(self, http: Any, api_key: str, model: str) -> None:
        self.http, self.api_key, self.model = http, api_key, model

    async def transcribe(self, wav: bytes, language: str = DEFAULT_LANGUAGE) -> str:
        form = aiohttp.FormData()
        form.add_field("file", wav, filename="audio.wav", content_type="audio/wav")
        form.add_field("model", self.model)
        # Naming the language beats autodetect on clips this short — that is
        # the whole point of the setting. Re-normalized here rather than
        # trusted: this class is injected, and OpenAI rejects a bad code with
        # a 400 that would surface as "stt-failed" on the key.
        form.add_field("language", normalize_language(language))
        try:
            async with self.http.post(
                URL, data=form, headers={"Authorization": f"Bearer {self.api_key}"}
            ) as resp:
                if resp.status != 200:
                    raise TranscribeError(f"transcription failed: {resp.status}")
                body = await resp.json()
        except TranscribeError:
            raise
        except Exception as exc:  # noqa: BLE001 — network faults are one failure
            raise TranscribeError(f"transcription request failed: {exc}") from exc
        return (body.get("text") or "").strip()
