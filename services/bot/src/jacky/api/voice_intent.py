"""Deterministic voice grammar: transcript -> list[Action].

An ordered table matcher, kept as the OFFLINE FALLBACK for when the LLM
interpreter is unreachable: free, instant, and testable, so an OpenAI outage
degrades to basic commands instead of breaking the key. `parse_fallback`
speaks the LLM's action vocabulary, so both paths agree on what a verb means.
Song search is the ONE free-form case: anything matching no command becomes a
search query.

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

from jacky.api.voice_actions import Action

_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9]")


def normalize_playlist_name(name: str) -> str:
    """Loose key for matching spoken names to saved ones: 'Chill Vibes' and
    'chill vibes' must be the same playlist."""
    return _NON_ALNUM.sub("", name.lower())


_FALLBACK_EXACT = {
    "skip": Action("skip"),
    "next": Action("skip"),
    "skip track": Action("skip"),
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
    "session code": Action("session_info"),
    "post the session": Action("session_info"),
    "open the dashboard": Action("open_dashboard"),
    "open dashboard": Action("open_dashboard"),
}

_FALLBACK_PLAYLIST_PREFIXES = (
    ("play playlist ", "now"),
    ("add playlist ", "end"),
    ("queue playlist ", "end"),
)
_FALLBACK_PLAY_PREFIXES = (("play ", "now"), ("add ", "end"), ("queue ", "end"))


def parse_fallback(transcript: str) -> list[Action]:
    """Offline path used when the LLM is unreachable. Same semantics as the
    LLM vocabulary, so both paths agree on what 'play' means."""
    text = transcript.strip().strip(".!?,").strip()
    if not text:
        return []
    lowered = text.lower()
    norm = _WS.sub(" ", _PUNCT.sub(" ", lowered)).strip()
    if norm in _FALLBACK_EXACT:
        return [_FALLBACK_EXACT[norm]]

    for prefix, placement in _FALLBACK_PLAYLIST_PREFIXES:
        if lowered.startswith(prefix):
            name = text[len(prefix) :].strip()
            if name:
                return [Action("playlist", name=name, placement=placement)]

    for prefix, placement in _FALLBACK_PLAY_PREFIXES:
        if lowered.startswith(prefix):
            query = text[len(prefix) :].strip()
            if query and query.lower() != "playlist":
                # A trailing "next" overrides the verb's placement, so
                # "play X next" means front-of-queue rather than interrupt.
                if query.lower().endswith(" next"):
                    return [
                        Action("play", query=query[:-5].strip(), placement="next")
                    ]
                return [Action("play", query=query, placement=placement)]

    return [Action("play", query=text, placement="now")]
