"""Voice grammar: transcript -> Intent.

Deliberately a deterministic ordered matcher rather than an LLM. The
requirement is structured phrases with consistent behavior, and a table is
free, instant, and testable. Song search is the ONE free-form case: anything
matching no command becomes a search query.

Note the asymmetry: the ARGUMENT is always sliced from the original text, so a
query like "AC/DC" survives intact.

Matching is layered, not uniform:
- exact commands match a fully normalized transcript (punctuation stripped,
  whitespace collapsed);
- prefixed commands match the lowercased text only, so "Play, playlist X"
  (comma after the verb) or a doubled space falls through to search.
  Normalizing those too would require mapping offsets back into the original
  text, since the ARGUMENT must keep its punctuation ("AC/DC").
"""

import re
from dataclasses import dataclass

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9]")


@dataclass(frozen=True)
class Intent:
    kind: str  # skip|pause|resume|volume_up|volume_down|playlist_play|playlist_add|search
    arg: str = ""


_EXACT = {
    "skip": "skip",
    "next": "skip",
    "skip track": "skip",
    "pause": "pause",
    "resume": "resume",
    "unpause": "resume",
    "continue": "resume",
    "volume up": "volume_up",
    "louder": "volume_up",
    "turn it up": "volume_up",
    "volume down": "volume_down",
    "quieter": "volume_down",
    "turn it down": "volume_down",
}

# Checked before the plain verbs, so "playlist" right after the verb wins.
_PLAYLIST_PREFIXES = (
    ("play playlist ", "playlist_play"),
    ("add playlist ", "playlist_add"),
    ("queue playlist ", "playlist_add"),
)
_SEARCH_PREFIXES = ("play ", "add ", "queue ")


def normalize_playlist_name(name: str) -> str:
    """Loose key for matching spoken names to saved ones: 'Chill Vibes' and
    'chill vibes' must be the same playlist."""
    return _NON_ALNUM.sub("", name.lower())


def parse_intent(transcript: str) -> Intent | None:
    """None when there is nothing to act on (silence or pure punctuation)."""
    text = transcript.strip().strip(".!?,").strip()
    if not text:
        return None

    lowered = text.lower()
    # Normalized only for MATCHING; arguments are sliced from `text` below so
    # a query like "AC/DC" keeps its punctuation.
    norm = _WS.sub(" ", _PUNCT.sub(" ", lowered)).strip()
    if norm in _EXACT:
        return Intent(_EXACT[norm])

    for prefix, kind in _PLAYLIST_PREFIXES:
        if lowered.startswith(prefix):
            # No emptiness guard needed: `text` is already stripped, so
            # nothing can start with "play playlist " and leave "" behind.
            return Intent(kind, text[len(prefix) :].strip())

    for prefix in _SEARCH_PREFIXES:
        if lowered.startswith(prefix):
            arg = text[len(prefix) :].strip()
            # "<verb> playlist" with no name is an incomplete playlist
            # command, not a search for the literal word "playlist": fall
            # through to the whole-transcript case below.
            if arg and arg.lower() != "playlist":
                return Intent("search", arg)

    # The one free-form case.
    return Intent("search", text)
