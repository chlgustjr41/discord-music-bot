"""Turn a transcript into a list of actions using an LLM.

Structured outputs constrain the model at decode time, but the result is still
run through validate_actions — the model is untrusted input. The system prompt
carries only the rules the schema cannot express.
"""

import json
from typing import Any

from jacky.api.voice_actions import ACTION_SCHEMA, MAX_ACTIONS, Action, validate_actions

URL = "https://api.openai.com/v1/chat/completions"

SYSTEM_PROMPT = (
    "You convert a spoken music command into actions for a Discord music bot. "
    "Rules:\n"
    "- Extract search terms LITERALLY from the user's words. Never invent a "
    "song, artist, or playlist the user did not say.\n"
    "- 'play X' means play X immediately (placement 'now'). 'play X next' "
    "means placement 'next'. 'add X' or 'queue X' means placement 'end'.\n"
    "- Stopping, halting or 'turn it off' means pause. There is no way to end "
    "the session or delete anything; never try.\n"
    "- 'clear' refers only to clearing the queue.\n"
    f"- Emit at most {MAX_ACTIONS} actions, in the order the user said them.\n"
    "- If the user simply names music with no verb, treat it as play 'now'."
)


class InterpretError(Exception):
    pass


class LlmIntentInterpreter:
    def __init__(self, http: Any, api_key: str, model: str) -> None:
        self.http, self.api_key, self.model = http, api_key, model

    async def interpret(self, transcript: str) -> list[Action]:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": transcript},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "voice_actions",
                    "strict": True,
                    "schema": ACTION_SCHEMA,
                },
            },
        }
        try:
            async with self.http.post(
                URL, json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
            ) as resp:
                if resp.status != 200:
                    # Status only — never the transcript.
                    raise InterpretError(f"interpretation failed: {resp.status}")
                payload = await resp.json()
        except InterpretError:
            raise
        except Exception as exc:  # noqa: BLE001 — one failure mode for the caller
            raise InterpretError(f"interpretation request failed: {exc}") from exc

        try:
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise InterpretError("unusable interpretation response") from exc

        actions = validate_actions(parsed.get("actions"))
        if not actions:
            raise InterpretError("no usable actions")
        return actions
