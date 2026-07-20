from ears.intents import COMMAND_WORDS
from ears.phrases import (
    build_active_grammar,
    build_passive_grammar,
    normalize_phrase,
    validate_phrase,
)

VOCAB = {"hey", "jacky", "okay", "dj", "music"}          # stand-in model vocab
KNOWS = VOCAB.__contains__


def test_normalize():
    assert normalize_phrase("  Hey, JACKY! ") == "hey jacky"


def test_validate_ok():
    assert validate_phrase("hey jacky", KNOWS) == []


def test_validate_word_count():
    assert validate_phrase("jacky", KNOWS) == ["need 2-4 words"]
    assert validate_phrase("a b c d e", KNOWS) == ["need 2-4 words"]


def test_validate_unknown_words():
    assert validate_phrase("hey zorblatt", KNOWS) == ["unknown word: zorblatt"]


def test_passive_grammar_is_phrase_plus_unk():
    import json
    assert json.loads(build_passive_grammar("hey jacky")) == ["hey jacky", "[unk]"]


def test_active_grammar_includes_commands_and_unk():
    import json
    g = json.loads(build_active_grammar())
    assert "[unk]" in g
    for w in COMMAND_WORDS:
        assert w in g
