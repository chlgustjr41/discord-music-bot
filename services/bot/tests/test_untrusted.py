"""Coercion of values read from client-writable Firestore documents.

Everything under servers/{guildId} can be written by a web client, so these
helpers are fed whatever a stranger chose to store. The contract under test
is uniform: a usable value, or None — never an exception.
"""

import math

import pytest

from jacky.state.untrusted import (
    MAX_SEEK_SECONDS,
    as_discord_id,
    as_seek_seconds,
    as_snowflake,
    as_volume,
    shape,
)

JUNK = [None, "", "loud", "12abc", [], [50], {}, {"volume": 50},
        pytest.param(object(), id="object"),
        math.nan, math.inf, -math.inf, True, False, b"50",
        # Past the interpreter's int-conversion digit limit: ValueError.
        pytest.param("9" * 5000, id="5000-digit-string")]


@pytest.mark.parametrize("value", JUNK)
def test_junk_volume_is_the_default_never_an_exception(value):
    assert as_volume(value) == 80
    assert as_volume(value, default=None) is None


@pytest.mark.parametrize("value,expected", [
    (0, 0), (80, 80), (100, 100),          # 0 is a real level (muted), not "unset"
    (-1, 0), (-10**9, 0),                  # clamped, both ends
    (101, 100), (10**30, 100),
    (55.9, 55),                            # Firestore stores JS numbers as doubles
    ("70", 70), (" 70 ", 70),              # int() accepted these before; still does
])
def test_volume_is_coerced_and_clamped(value, expected):
    assert as_volume(value) == expected
    assert as_volume(value, default=None) == expected


@pytest.mark.parametrize("value", JUNK)
def test_junk_seek_is_ignored(value):
    assert as_seek_seconds(value) is None


@pytest.mark.parametrize("value,expected", [
    (0, 0), (90, 90), (90.7, 90), ("90", 90),
    (-0.4, 0), (-5, 0),                    # a slider can undershoot; that is "the start"
    (MAX_SEEK_SECONDS, MAX_SEEK_SECONDS),
])
def test_seek_is_coerced_and_floored_at_zero(value, expected):
    assert as_seek_seconds(value) == expected


@pytest.mark.parametrize("value", [MAX_SEEK_SECONDS + 1, 10**12, 10**400, 1e300])
def test_absurd_seek_is_ignored_not_clamped(value):
    """Clamping would turn a nonsense write into a real jump to the end of
    the track; and an unbounded value overflows the startedAt arithmetic."""
    assert as_seek_seconds(value) is None


SNOWFLAKE = "123456789012345678"


@pytest.mark.parametrize("value,expected", [
    (SNOWFLAKE, 123456789012345678),
    (123456789012345678, 123456789012345678),   # an int is fine, it has no content
    ("1" * 15, int("1" * 15)),
    ("1" * 22, int("1" * 22)),
])
def test_snowflakes_are_accepted(value, expected):
    assert as_snowflake(value) == expected
    assert as_discord_id(value) == expected


@pytest.mark.parametrize("value", [
    None, "", "abc", "not-a-guild", SNOWFLAKE + "x", " " + SNOWFLAKE, SNOWFLAKE + "\n",
    "-" + SNOWFLAKE, "+" + SNOWFLAKE, "1_000_000_000_000_000", "1e17", "0x" + "1" * 16,
    "١٢٣٤٥٦٧٨٩٠١٢٣٤٥٦٧٨",     # int() parses Arabic-Indic digits; an id never has them
    "²" * 18,                  # str.isdigit() is True for these; int() raises
    "1" * 14, "1" * 23, pytest.param("9" * 5000, id="5000-digit-string"),
    1.2345678901234568e17, True, [], {}, [SNOWFLAKE], b"123456789012345678",
    -123456789012345678,
])
def test_non_snowflakes_are_rejected(value):
    assert as_snowflake(value) is None


@pytest.mark.parametrize("value,expected", [("42", 42), (42, 42), ("0", 0)])
def test_discord_id_accepts_short_digit_ids_that_a_snowflake_check_would_not(
    value, expected
):
    """For values only ever LOOKED UP in the bot's own cache: an implausible
    id simply finds nothing, so shape is not policed — only safety."""
    assert as_discord_id(value) == expected
    assert as_snowflake(value) is None


@pytest.mark.parametrize("value", [
    None, "", "abc", "12abc", " 42", "4 2", "-42", "+42", "4.2", "²", "1" * 23,
    4.2, True, [], {}, -42,
])
def test_discord_id_rejects_anything_that_is_not_plain_digits(value):
    assert as_discord_id(value) is None


def test_shape_describes_a_value_without_revealing_it():
    secret = "token-ABC-123"
    described = shape(secret)
    assert secret not in described and "ABC" not in described
    assert described == "str, 13 chars, not all digits"
    assert shape("123456") == "str, 6 chars, all digits"
    assert shape(None) == "NoneType"
    assert shape({"a": secret}) == "dict"
    assert shape(12) == "int"
    # A huge value must not make the log line huge.
    assert len(shape("x" * 100_000)) < 60
