"""Wake-phrase normalization/validation and Vosk grammar builders.

Vosk grammars are JSON lists of allowed utterances; "[unk]" absorbs everything
else so random speech doesn't get force-matched onto the wake phrase.
"""

import json
import re
from collections.abc import Callable

from ears.intents import COMMAND_WORDS

_WORD = re.compile(r"[a-z']+")


def normalize_phrase(raw: str) -> str:
    return " ".join(_WORD.findall(raw.lower()))


def validate_phrase(raw: str, knows_word: Callable[[str], bool]) -> list[str]:
    """Return a list of problems; empty list means valid (2-4 known words)."""
    words = normalize_phrase(raw).split()
    if not 2 <= len(words) <= 4:
        return ["need 2-4 words"]
    return [f"unknown word: {w}" for w in words if not knows_word(w)]


def build_passive_grammar(phrase: str) -> str:
    return json.dumps([normalize_phrase(phrase), "[unk]"])


def build_active_grammar() -> str:
    return json.dumps([*COMMAND_WORDS, "[unk]"])
