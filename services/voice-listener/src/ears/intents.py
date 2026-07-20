"""Transcript -> Intent. Pure functions; grammar lives in COMMAND_WORDS."""

from dataclasses import dataclass

# Words the ACTIVE-mode Vosk grammar is allowed to hear (plus free dictation
# via "[unk]" for the play-title tail — see engine.build_active_grammar).
COMMAND_WORDS = [
    "skip", "next", "pause", "resume", "play", "stop",
    "volume", "up", "down", "louder", "quieter", "track", "this", "the", "song",
]

_EXACT = {
    "pause": "pause",
    "resume": "resume",
    "stop": "stop",
    "louder": "volume_up",
    "quieter": "volume_down",
}


@dataclass(frozen=True)
class Intent:
    name: str
    arg: str | None


def parse_intent(text: str) -> Intent | None:
    words = text.lower().split()
    if not words:
        return None
    head = words[0]
    if head in ("skip", "next"):
        return Intent("skip", None)
    if head in _EXACT:
        return Intent(_EXACT[head], None)
    if head == "volume" and len(words) >= 2 and words[1] in ("up", "down"):
        return Intent(f"volume_{words[1]}", None)
    if head == "play":
        tail = " ".join(words[1:]).strip()
        return Intent("play", tail) if tail else Intent("resume", None)
    return None
