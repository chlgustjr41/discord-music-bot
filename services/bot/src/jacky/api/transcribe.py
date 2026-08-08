"""OpenAI speech-to-text. Injectable so tests never touch the network.

Audio is streamed straight through — never written to disk here or anywhere
else in the request path.
"""

from typing import Any

import aiohttp

URL = "https://api.openai.com/v1/audio/transcriptions"


class TranscribeError(Exception):
    pass


class OpenAITranscriber:
    def __init__(self, http: Any, api_key: str, model: str) -> None:
        self.http, self.api_key, self.model = http, api_key, model

    async def transcribe(self, wav: bytes) -> str:
        form = aiohttp.FormData()
        form.add_field("file", wav, filename="audio.wav", content_type="audio/wav")
        form.add_field("model", self.model)
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
