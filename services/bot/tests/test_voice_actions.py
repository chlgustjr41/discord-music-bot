"""The security boundary: model output is untrusted and re-validated here."""

from jacky.api.voice_actions import ACTION_SCHEMA, Action, validate_actions


def test_valid_actions_pass_through():
    got = validate_actions([
        {"action": "play", "query": "bohemian rhapsody", "placement": "now"},
        {"action": "skip", "count": 2},
    ])
    assert got == [
        Action("play", query="bohemian rhapsody", placement="now"),
        Action("skip", count=2),
    ]


def test_unknown_verbs_are_dropped_not_executed():
    """A confused or adversarial model must not be able to invent verbs."""
    got = validate_actions([
        {"action": "delete_playlist", "name": "chill"},
        {"action": "drop_database"},
        {"action": "skip"},
    ])
    assert got == [Action("skip", count=1)]


def test_deletion_is_unreachable_by_any_shape():
    for raw in (
        {"action": "delete"},
        {"action": "remove_playlist", "name": "x"},
        {"action": "clear", "target": "playlists"},
        {"action": "stop"},
    ):
        assert validate_actions([raw]) == []


def test_placement_defaults_to_now_and_rejects_junk():
    assert validate_actions([{"action": "play", "query": "x"}])[0].placement == "now"
    assert validate_actions(
        [{"action": "play", "query": "x", "placement": "sideways"}]
    )[0].placement == "now"


def test_numeric_fields_are_clamped():
    assert validate_actions([{"action": "skip", "count": 999}])[0].count == 10
    assert validate_actions([{"action": "skip", "count": 0}])[0].count == 1
    assert validate_actions([{"action": "volume", "level": 500}])[0].level == 100
    assert validate_actions([{"action": "volume", "level": -20}])[0].level == 0


def test_more_than_five_actions_are_truncated():
    raw = [{"action": "skip"} for _ in range(9)]
    assert len(validate_actions(raw)) == 5


def test_malformed_entries_do_not_kill_the_batch():
    got = validate_actions(["nonsense", None, 42, {"no_action_key": 1},
                            {"action": "pause"}])
    assert got == [Action("pause")]


def test_actions_requiring_text_are_dropped_when_it_is_missing():
    assert validate_actions([{"action": "play"}]) == []
    assert validate_actions([{"action": "playlist", "name": "  "}]) == []


def test_non_list_input_is_empty():
    assert validate_actions(None) == []
    assert validate_actions({"action": "skip"}) == []


def test_schema_declares_the_closed_vocabulary():
    """The schema is what constrains the model at decode time; if a verb is
    missing here the model cannot emit it at all."""
    verbs = ACTION_SCHEMA["properties"]["actions"]["items"]["properties"]["action"]["enum"]
    assert set(verbs) == {
        "play", "playlist", "skip", "pause", "resume",
        "volume", "shuffle", "clear_queue", "loop",
    }
    assert not any("delete" in v or "remove" in v for v in verbs)
