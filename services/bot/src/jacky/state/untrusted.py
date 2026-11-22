"""Coercion for values read out of client-writable Firestore documents.

Everything under servers/{guildId} can be written by a web client (the
session code is the only capability), so a field the bot wrote as an int can
come back as a string, a map, null, NaN, or a megabyte of text. One such
value must not be able to break playback for the server it sits in — or, on
the collection-wide summon watcher, for every server.

The contract is the same for every helper here: a usable value, or None for
"ignore this". Nothing in this module raises on any input.
"""

import re

DEFAULT_VOLUME = 80

# Longer than anything the enabled sources serve (YouTube caps uploads at 12 h),
# and far inside what the startedAt arithmetic in the listener can represent.
MAX_SEEK_SECONDS = 24 * 60 * 60

# ASCII digits spelled out: str.isdigit() and int() both accept other
# scripts' digits ("²", "١٢٣"), and a Discord id never contains them.
_DIGITS_RE = re.compile(r"[0-9]+")
_DISCORD_ID_RE = re.compile(r"[0-9]{1,22}")
# Snowflakes are 17-20 digits today; the margin covers the earliest ids and
# the next few decades without admitting arbitrary numbers.
_SNOWFLAKE_RE = re.compile(r"[0-9]{15,22}")


def _as_int(value: object) -> int | None:
    # bool is an int subclass, and True is not a volume. bytes would parse,
    # but nothing legitimate stores one.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    # OverflowError: int(inf). ValueError also covers NaN and digit strings
    # past the interpreter's int-conversion limit.
    except (ValueError, OverflowError):
        return None


def as_volume(value: object, default: int | None = DEFAULT_VOLUME) -> int | None:
    """A 0-100 volume, or `default` when the value is not a number at all.

    Out-of-range numbers are clamped rather than refused: 0 is a real level
    (muted) and "louder than 100" has an obvious meaning. Pass default=None
    to tell "not a number" apart from a real level.
    """
    number = _as_int(value)
    if number is None:
        return default
    return max(0, min(100, number))


def as_seek_seconds(value: object) -> int | None:
    """A seek target in whole seconds, or None to ignore the request.

    Below zero is "the start" (a slider can undershoot). Beyond
    MAX_SEEK_SECONDS is ignored, NOT clamped: clamping would turn a nonsense
    write into a real jump to the end of the track.
    """
    number = _as_int(value)
    if number is None or number > MAX_SEEK_SECONDS:
        return None
    return max(0, number)


def _id_matching(pattern: re.Pattern, value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    text = str(value)
    return int(text) if pattern.fullmatch(text) else None


def as_discord_id(value: object) -> int | None:
    """Plain ASCII digits as an int, or None. For ids that are only ever
    LOOKED UP in the bot's own guild/channel cache: an implausible id simply
    finds nothing, so all this guarantees is that the conversion is safe."""
    return _id_matching(_DISCORD_ID_RE, value)


def as_snowflake(value: object) -> int | None:
    """Like as_discord_id, but the value must also be SHAPED like a Discord
    snowflake. For ids that cause work merely by existing — the summon
    watcher schedules a join attempt per document — where "any number" would
    let a stranger's made-up document cost a Firestore read and write."""
    return _id_matching(_SNOWFLAKE_RE, value)


def shape(value: object) -> str:
    """What a rejected value looked like, safe to log: its type and, for a
    string, its length and whether it was all digits. Never the value — it
    was chosen by whoever wrote the document, and logs are not theirs to
    write into."""
    if isinstance(value, str):
        digits = "all digits" if _DIGITS_RE.fullmatch(value) else "not all digits"
        return f"str, {len(value)} chars, {digits}"
    return type(value).__name__
