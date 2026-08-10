"""Deterministic voice grammar: transcript -> StructuredParse.

Structure decides FIRST. An ordered table matcher over a closed vocabulary:
free, instant, testable, and not subject to a model changing its mind. What it
resolves never reaches the LLM at all.

What it does NOT do is guess. The predecessor (`voice_intent.parse_fallback`)
ended in a catch-all — "anything matching no command becomes a search query" —
which made "next song" a search for *song*, and made every misheard phrase a
search with placement "now", i.e. an interruption of whatever was playing.
That branch is DELETED, not reordered: a catch-all that fires only sometimes is
the same bug with a smaller blast radius. Unresolved input returns
`resolved=False` with no actions, and the caller reasons about it or does
nothing.

Note the asymmetry: the ARGUMENT is always sliced from the ORIGINAL text, so a
query like "AC/DC" survives with its punctuation and case intact.

Matching is layered, not uniform:
- exact commands match a fully normalized transcript (punctuation replaced by
  a space, whitespace collapsed);
- prefixed commands match the lowercased text only, so "Play, playlist X"
  (comma after the verb) falls through to unresolved. Normalizing those too
  would require mapping offsets back into the original text, since the
  ARGUMENT must keep its punctuation.
"""

import re
from dataclasses import dataclass

from jacky.api.voice_actions import Action

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9]")
_WORD = re.compile(r"[a-z0-9]+")


def normalize_playlist_name(name: str) -> str:
    """Loose key for matching spoken names to saved ones: 'Chill Vibes' and
    'chill vibes' must be the same playlist."""
    return _NON_ALNUM.sub("", name.lower())


_EXACT = {
    "pause": Action("pause"),
    # Stop-like speech pauses: ending a session is not a voice capability, and
    # one misrecognition must not be able to end it.
    "stop": Action("pause"),
    "resume": Action("resume"),
    "unpause": Action("resume"),
    "continue": Action("resume"),
    "louder": Action("volume", delta=10),
    "volume up": Action("volume", delta=10),
    "turn it up": Action("volume", delta=10),
    "quieter": Action("volume", delta=-10),
    "volume down": Action("volume", delta=-10),
    "turn it down": Action("volume", delta=-10),
    "shuffle": Action("shuffle"),
    "shuffle the queue": Action("shuffle"),
    "shuffle queue": Action("shuffle"),
    "clear": Action("clear_queue"),
    "clear the queue": Action("clear_queue"),
    "clear queue": Action("clear_queue"),
    # Punctuation normalizes to a SPACE, not to nothing, so the key for
    # "what's playing" is "what s playing". The apostrophe-less spelling is
    # kept too because transcribers emit both.
    "what s playing": Action("now_playing"),
    "whats playing": Action("now_playing"),
    "what is playing": Action("now_playing"),
    "now playing": Action("now_playing"),
    "post the session code": Action("session_info"),
    "post session code": Action("session_info"),
    "session code": Action("session_info"),
    "post the session": Action("session_info"),
    "open the dashboard": Action("open_dashboard"),
    "open dashboard": Action("open_dashboard"),
    # Bare "repeat"/"loop" mean the track, which is what people say when they
    # want the song they are hearing again.
    "repeat": Action("loop", mode="track"),
    "repeat this song": Action("loop", mode="track"),
    "repeat this track": Action("loop", mode="track"),
    "loop": Action("loop", mode="track"),
    "loop track": Action("loop", mode="track"),
    "loop this song": Action("loop", mode="track"),
    "loop the track": Action("loop", mode="track"),
    "loop queue": Action("loop", mode="queue"),
    "loop the queue": Action("loop", mode="queue"),
    "repeat queue": Action("loop", mode="queue"),
    "loop off": Action("loop", mode="off"),
    "repeat off": Action("loop", mode="off"),
    "stop looping": Action("loop", mode="off"),
}

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

# LEADING "next" is media control. The filler noun ("song", "track") is part
# of the command, not a search query — that single positional rule is what
# stops "next song" becoming a search for "song". TRAILING "next" is
# placement, and is handled down in the prefix section.
_SKIP = re.compile(
    r"^(?:skip|next)"
    r"(?:\s+(?P<n>\d{1,2}|" + "|".join(_NUMBER_WORDS) + r"))?"
    r"(?:\s+(?:songs?|tracks?))?$"
)
# Absolute volume takes digits only; the spoken-number forms people actually
# use ("volume up", "louder") are relative and live in the exact table.
_VOLUME = re.compile(r"^(?:set\s+)?volume\s+(?:to\s+|at\s+)?(?P<n>\d{1,3})$")

_PLAYLIST_PREFIXES = (
    ("play playlist ", "now"),
    ("add playlist ", "end"),
    ("queue playlist ", "end"),
)
_PLAY_PREFIXES = (("play ", "now"), ("add ", "end"), ("queue ", "end"))

# Words the reasoning layer is told about when the grammar gives up, so
# structure informs reasoning instead of competing with it.
_VOCABULARY = frozenset({
    "play", "playlist", "add", "queue", "next", "skip", "pause", "stop",
    "resume", "continue", "unpause", "volume", "louder", "quieter", "up",
    "down", "shuffle", "clear", "session", "code", "dashboard", "playing",
    "repeat", "loop", "track", "song",
})


@dataclass(frozen=True)
class StructuredParse:
    """Result of the deterministic pass.

    `resolved` is not `bool(actions)` restated for the reader's benefit: it is
    the answer the reasoning layer branches on, and it stays False for every
    utterance the grammar declined to interpret.
    """

    actions: list[Action]   # empty when unresolved
    keywords: list[str]     # vocabulary words found, passed to the LLM as hints
    resolved: bool


def _keywords(norm: str) -> list[str]:
    out: list[str] = []
    for word in _WORD.findall(norm):
        if word in _VOCABULARY and word not in out:
            out.append(word)
    return out


def _split_trailing_next(text: str) -> tuple[str, bool]:
    """TRAILING "next" is placement: "play X next" is front-of-queue."""
    if text.lower().endswith(" next"):
        return text[: -len(" next")].strip(), True
    return text, False


def parse_structured(transcript: str) -> "StructuredParse":
    """Resolve the closed vocabulary, or resolve nothing.

    There is deliberately NO free-form branch: an utterance that matches no
    command is unresolved, never a search.
    """
    text = transcript.strip().strip(".!?,").strip()
    if not text:
        return StructuredParse([], [], False)
    lowered = text.lower()
    norm = _WS.sub(" ", _PUNCT.sub(" ", lowered)).strip()
    keywords = _keywords(norm)

    def resolved(actions: list[Action]) -> StructuredParse:
        return StructuredParse(actions, keywords, True)

    if norm in _EXACT:
        return resolved([_EXACT[norm]])

    skip = _SKIP.match(norm)
    if skip:
        raw = skip.group("n")
        if raw is None:
            count = 1
        else:
            count = _NUMBER_WORDS.get(raw) or int(raw)
        return resolved([Action("skip", count=max(1, min(10, count)))])

    volume = _VOLUME.match(norm)
    if volume:
        return resolved([
            Action("volume", level=max(0, min(100, int(volume.group("n")))))
        ])

    for prefix, placement in _PLAYLIST_PREFIXES:
        if lowered.startswith(prefix):
            name, trailing_next = _split_trailing_next(text[len(prefix):].strip())
            if name:
                return resolved([Action(
                    "playlist",
                    name=name,
                    placement="next" if trailing_next else placement,
                )])

    for prefix, placement in _PLAY_PREFIXES:
        if lowered.startswith(prefix):
            query, trailing_next = _split_trailing_next(text[len(prefix):].strip())
            # "play playlist" with no name is an incomplete playlist command,
            # not a song search for the literal word "playlist". "play next"
            # is the same shape: _split_trailing_next needs a LEADING space,
            # so a bare trailing "next" is not stripped as placement and
            # would become a YouTube search for the literal word "next" —
            # with placement "now", replacing the track, since "play" really
            # was said and enforce_intent allows it.
            #
            # DECISION — unresolved, not skip. "play next" most likely means
            # skip, but this module does not guess (see the docstring), and
            # resolving it here is what makes it unfixable: the reasoning
            # layer only ever sees what the grammar declined. Unresolved, it
            # reaches the LLM with keywords ["play", "next"] and can be read
            # as a skip, an incomplete "play X next", or nothing at all.
            if query and query.lower() not in ("playlist", "next"):
                # DECISION — "play next song": "next" here neither leads the
                # transcript (media control) nor trails it (placement), so it
                # is treated as part of the TITLE: play "next song" now. A
                # user asking for a track literally called "next song" is
                # indistinguishable from one asking to queue "song" next, and
                # taking the words as spoken keeps the argument-slicing rule
                # intact — the argument is whatever sits between the verb and
                # a trailing placement word.
                return resolved([
                    Action(
                        "play",
                        query=query,
                        placement="next" if trailing_next else placement,
                    )
                ])

    # No catch-all. "I don't know" does nothing.
    return StructuredParse([], keywords, False)
