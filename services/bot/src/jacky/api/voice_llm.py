"""Classify a transcript onto the closed action vocabulary using an LLM.

This layer runs ONLY on what the deterministic grammar could not resolve, and
its job is classification, not generation: it maps an utterance onto verbs
that already exist, or it says nothing. It is given the vocabulary words the
grammar recognized so that structure informs the reasoning rather than
competing with it.

Structured outputs constrain the model at decode time, but the result is still
run through validate_actions — the model is untrusted input. The system prompt
carries only the rules the schema cannot express.

An EMPTY action list is a valid answer, not a failure. It used to raise, which
made the caller treat "I could not classify this" as an outage and reach for a
fallback — and the fallback searched. Transport faults, non-200s and malformed
JSON still raise, because those are genuinely "no answer at all".
"""

import json
from collections.abc import Sequence
from typing import Any

from jacky.api.voice_actions import ACTION_SCHEMA, MAX_ACTIONS, Action, validate_actions

URL = "https://api.openai.com/v1/chat/completions"

SYSTEM_PROMPT = (
    "You CLASSIFY a spoken command onto a fixed set of actions for a Discord "
    "music bot. You never invent an action, and you never invent a song, "
    "artist or playlist the user did not say. Rules:\n"
    "- Extract search terms LITERALLY from the user's words.\n"
    "- 'next', 'next song' and 'next track' at the START mean SKIP: they are "
    "media control, not a request to search for the word 'song'.\n"
    "- 'playlist' always refers to one of the session's saved playlists — use "
    "the playlist action, never a search.\n"
    "- 'play X' replaces what is playing (placement 'now'). 'play X next' "
    "puts X at the front of the queue (placement 'next'). 'add X' or "
    "'queue X' puts X at the end (placement 'end'). The same three "
    "placements apply to playlists.\n"
    "- A bare noun phrase with NO verb is NOT a search. If the user only "
    "names something, you do not know what they wanted done with it.\n"
    "- Stopping, halting or 'turn it off' means pause. There is no way to end "
    "the session or delete anything; never try.\n"
    "- 'clear' refers only to clearing the queue.\n"
    f"- Emit at most {MAX_ACTIONS} actions, in the order the user said them.\n"
    "- If you are not confident what was asked for, return an EMPTY list of "
    "actions. That is a correct answer, and it is much better than guessing: "
    "a wrong guess interrupts the music the user is listening to."
)


class InterpretError(Exception):
    pass


class LlmIntentInterpreter:
    def __init__(self, http: Any, api_key: str, model: str) -> None:
        self.http, self.api_key, self.model = http, api_key, model

    async def interpret(
        self, transcript: str, keywords: Sequence[str] = ()
    ) -> list[Action]:
        user = transcript
        if keywords:
            # A hint, not an instruction: the words are reported as what the
            # parser SAW, so the model can weigh them against the utterance
            # instead of being told an answer the grammar already declined to
            # give.
            user += (
                "\n\nVocabulary words the command parser recognized: "
                + ", ".join(keywords)
            )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
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

        # No emptiness check: "nothing" is an answer. See the module docstring.
        return validate_actions(parsed.get("actions"))
