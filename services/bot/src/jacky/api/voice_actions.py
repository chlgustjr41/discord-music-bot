"""Closed action vocabulary for voice commands, plus the validator.

This is the security boundary. The model's output is UNTRUSTED input: the
JSON schema constrains it at decode time, but that guarantee is re-checked
here regardless. A schema the model should obey is not the same as one it did.

There is deliberately no verb for deleting a playlist, history, or a session.
That is the guarantee — not a prompt instruction the model might ignore.
`clear_queue` is the only destructive action and empties only the queue.
"""

import re
from dataclasses import dataclass, replace

MAX_ACTIONS = 5
MAX_SKIP = 10
PLACEMENTS = ("now", "next", "end")
LOOP_MODES = ("off", "track", "queue")

_VERBS = (
    "play", "playlist", "skip", "pause", "resume",
    "volume", "shuffle", "clear_queue", "loop",
    # Read-and-announce, plus one client-side action. None of these removes
    # anything — the no-deletion-verb guarantee is unchanged.
    "now_playing", "session_info", "open_dashboard",
)
_NEEDS_QUERY = ("play",)
_NEEDS_NAME = ("playlist",)


@dataclass(frozen=True)
class Action:
    action: str
    query: str = ""
    name: str = ""
    placement: str = "now"
    count: int = 1
    level: int | None = None
    delta: int | None = None
    mode: str = "off"


ACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["actions"],
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "action", "query", "name", "placement",
                    "count", "level", "delta", "mode",
                ],
                "properties": {
                    "action": {"type": "string", "enum": list(_VERBS)},
                    "query": {"type": "string"},
                    "name": {"type": "string"},
                    "placement": {"type": "string", "enum": list(PLACEMENTS)},
                    "count": {"type": "integer"},
                    # Nullable so the model can express "no absolute level" /
                    # "no relative delta" — validate_actions reads which of the
                    # two is None to tell absolute volume from relative.
                    # Strict mode has no optional fields, so optionality must
                    # be a union with null.
                    "level": {"type": ["integer", "null"]},
                    "delta": {"type": ["integer", "null"]},
                    "mode": {"type": "string", "enum": list(LOOP_MODES)},
                },
            },
        }
    },
}


def _clamp(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def validate_actions(raw) -> list[Action]:
    """Drop anything not in the vocabulary; clamp every number. Never raises."""
    if not isinstance(raw, list):
        return []
    out: list[Action] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        verb = entry.get("action")
        if verb not in _VERBS:
            continue
        query = str(entry.get("query") or "").strip()
        name = str(entry.get("name") or "").strip()
        if verb in _NEEDS_QUERY and not query:
            continue
        if verb in _NEEDS_NAME and not name:
            continue
        placement = entry.get("placement")
        mode = entry.get("mode")
        level = entry.get("level")
        delta = entry.get("delta")
        out.append(Action(
            action=verb,
            query=query,
            name=name,
            placement=placement if placement in PLACEMENTS else "now",
            count=_clamp(entry.get("count", 1), 1, MAX_SKIP, 1),
            level=None if level is None else _clamp(level, 0, 100, 80),
            delta=None if delta is None else _clamp(delta, -100, 100, 0),
            mode=mode if mode in LOOP_MODES else "off",
        ))
        if len(out) == MAX_ACTIONS:
            break
    return out


# Whole words only: "displayed" contains "play" but is not the verb, and
# "playlists" is not "playlist". Tokenizing beats a substring test or a
# hand-written \b regex per word, and it treats punctuation the same way the
# grammar's normalizer does.
#
# Unicode-aware (\w, not [a-z0-9]) so an accented word stays ONE token. With
# an ASCII class the Spanish "ponés" splits into "pon" + "s" and the verb
# "pon" matches a word that is not it — the same substring bug "displayed"
# already guards against, one alphabet over.
_WORD = re.compile(r"\w+")

# Play verbs per language, for the placement rule below. The Voice key offers
# these seven (see transcribe.LANGUAGES) and the route passes the same
# normalized code it sent to the transcriber.
#
# NOTE the imperative/infinitive pairs: speech is usually the imperative
# ("spiele"), but transcribers emit the infinitive often enough to be worth
# listing, and an extra verb only costs a missed downgrade on a phrasing that
# already contains a play verb.
_PLAY_VERBS = {
    "en": ("play",),
    "ko": ("재생", "틀어", "플레이"),
    "ja": ("再生", "かけて", "プレイ"),
    "es": ("reproduce", "reproducir", "pon", "toca"),
    "fr": ("joue", "jouer", "lance", "mets"),
    "de": ("spiel", "spiele", "spielen", "abspielen"),
    "zh": ("播放", "放"),
}

# Korean, Japanese and Chinese are not written with spaces around the verb
# stem — "재생해줘" is one token and "재생" is a prefix of it — so whole-word
# matching cannot work and these are matched as substrings. Latin-script
# languages keep whole-word matching, which is what makes "displayed" and
# "ponés" not count.
#
# The tradeoff is asymmetric per script: a substring FALSE POSITIVE (zh "放"
# also appears in 放松 "relax") costs an interrupt the user did not ask for,
# while a false negative costs only an append. "放" is kept because 放音乐 is
# how the command is ordinarily spoken; if that proves too loose in practice
# the fix is to drop the bare character, not to widen the others.
_SUBSTRING_LANGUAGES = frozenset({"ko", "ja", "zh"})


# Verbs that REPLACE what is currently playing when placement is "now".
# `playlist` belongs here as much as `play` does: VoiceIntentDispatcher's
# _playlist_action calls skip() when placement is "now" and something is
# playing, so it interrupts just as hard. The rule this module enforces is
# about interruption, not about which verb happens to carry it.
_INTERRUPTING = ("play", "playlist")


def _said_play(transcript: str, language: str) -> bool:
    """Did the user actually utter a play verb, in the language they set?"""
    verbs = _PLAY_VERBS.get(language)
    if verbs is None:
        # An unlisted language degrades to "assume they did", NOT to "no verb
        # exists". The latter would make the downgrade unconditional and leave
        # that language able only to append — silently, since the key still
        # reports success. That is exactly the failure this table exists to
        # remove, so the degradation must not reintroduce it for the next
        # language added to the dropdown before it is added here.
        return True
    lowered = transcript.lower()
    if language in _SUBSTRING_LANGUAGES:
        return any(verb in lowered for verb in verbs)
    return bool(set(_WORD.findall(lowered)) & set(verbs))


def enforce_intent(
    actions: list[Action], transcript: str, language: str = "en"
) -> list[Action]:
    """A second, purely structural pass, applied AFTER validate_actions.

    Two guarantees that do not depend on the model having cooperated:

    - an INTERRUPTING action (`play` or `playlist`) with placement "now" is
      downgraded to "end" unless the transcript actually contains a play verb
      IN `language`. The currently playing track is only overridden by an
      explicit play verb (or by a skip, which this function does not touch);
    - a `play` action becomes a `playlist` action when the transcript
      contains "playlist" — a saved playlist must never reach a YouTube
      search.

    The downgrade runs first, so the conversion carries the enforced
    placement rather than reinstating the one the model asked for.

    The playlist rule stays English-only on purpose: it keys off the word the
    grammar's own playlist table keys off, and mistaking a foreign word for
    "playlist" would route a real search to a playlist lookup that then finds
    nothing. The placement rule is the one with a silent failure mode.
    """
    said_play = _said_play(transcript, language)
    said_playlist = "playlist" in set(_WORD.findall(transcript.lower()))

    out: list[Action] = []
    for action in actions:
        if action.action not in _INTERRUPTING:
            out.append(action)
            continue
        placement = action.placement
        if placement == "now" and not said_play:
            placement = "end"
        if action.action == "play" and said_playlist:
            out.append(Action(
                "playlist", name=action.query, placement=placement,
            ))
        else:
            out.append(replace(action, placement=placement))
    return out
