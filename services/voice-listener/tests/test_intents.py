import pytest

from ears.intents import Intent, parse_intent

CASES = [
    ("skip", Intent("skip", None)),
    ("next", Intent("skip", None)),
    ("skip this track", Intent("skip", None)),
    ("pause", Intent("pause", None)),
    ("resume", Intent("resume", None)),
    ("play", Intent("resume", None)),          # bare "play" = resume
    ("stop", Intent("stop", None)),
    ("volume up", Intent("volume_up", None)),
    ("louder", Intent("volume_up", None)),
    ("volume down", Intent("volume_down", None)),
    ("quieter", Intent("volume_down", None)),
    ("play never gonna give you up", Intent("play", "never gonna give you up")),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_parse(text, expected):
    assert parse_intent(text) == expected


def test_unrecognized_returns_none():
    assert parse_intent("open the pod bay doors") is None
    assert parse_intent("") is None
