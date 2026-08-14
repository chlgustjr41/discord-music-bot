"""The security boundary: model output is untrusted and re-validated here."""

import pytest

from jacky.api.voice_actions import (
    ACTION_SCHEMA,
    Action,
    enforce_intent,
    validate_actions,
)


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
        "now_playing", "session_info", "queue_info", "status_info",
        "open_dashboard",
    }
    assert not any("delete" in v or "remove" in v for v in verbs)


def test_schema_obeys_openai_strict_mode_rules():
    """Structured Outputs with strict=true forbids optional fields: every key
    in `properties` must be in `required`, and every object must set
    `additionalProperties: false`. Violating either makes OpenAI reject the
    REQUEST — so every voice command would silently fall back to the
    deterministic parser. A faked HTTP client cannot catch this; only this
    test can."""
    def check(obj):
        assert obj.get("additionalProperties") is False
        assert set(obj["required"]) == set(obj["properties"])
        for prop in obj["properties"].values():
            if prop.get("type") == "object":
                check(prop)

    check(ACTION_SCHEMA)
    check(ACTION_SCHEMA["properties"]["actions"]["items"])


def test_volume_fields_stay_nullable_so_absolute_and_relative_differ():
    """If level/delta were non-nullable integers the model would have to emit
    both, and validate_actions could no longer tell 'set volume to 40' from
    'turn it up'."""
    props = ACTION_SCHEMA["properties"]["actions"]["items"]["properties"]
    assert props["level"]["type"] == ["integer", "null"]
    assert props["delta"]["type"] == ["integer", "null"]


def test_announce_and_client_verbs_validate():
    assert validate_actions([
        {"action": "now_playing"},
        {"action": "session_info"},
        {"action": "queue_info"},
        {"action": "status_info"},
        {"action": "open_dashboard"},
    ]) == [
        Action("now_playing"), Action("session_info"),
        Action("queue_info"), Action("status_info"), Action("open_dashboard"),
    ]


def test_new_verbs_need_no_arguments():
    """They are argument-free, so nothing may be dropped for a missing query."""
    assert validate_actions([{"action": "now_playing", "query": ""}]) == [
        Action("now_playing")
    ]


# ── enforce_intent: structural guarantees the model cannot opt out of ────


def test_now_is_downgraded_when_the_transcript_never_said_play():
    """The guarantee: the current track is only overridden if the command
    actually contains "play". A model that decided to interrupt anyway is
    corrected here, not asked nicely in a prompt."""
    got = enforce_intent(
        [Action("play", query="bohemian rhapsody", placement="now")],
        "bohemian rhapsody",
    )
    assert got == [Action("play", query="bohemian rhapsody", placement="end")]


def test_now_survives_when_the_transcript_said_play():
    got = enforce_intent(
        [Action("play", query="bohemian rhapsody", placement="now")],
        "play bohemian rhapsody",
    )
    assert got == [Action("play", query="bohemian rhapsody", placement="now")]


def test_play_is_matched_as_a_whole_word_not_a_substring():
    """"displayed" contains "play" but is not the verb."""
    got = enforce_intent(
        [Action("play", query="the sign", placement="now")],
        "displayed the sign",
    )
    assert got[0].placement == "end"


@pytest.mark.parametrize("placement", ["now", "next", "end"])
def test_playlist_in_the_transcript_can_never_become_a_search(placement):
    """"playlist" must never reach a YouTube search; the placement the model
    chose is preserved (after any "play"-less downgrade)."""
    got = enforce_intent(
        [Action("play", query="chill", placement=placement)],
        "play playlist chill",
    )
    assert got == [Action("playlist", name="chill", placement=placement)]


def test_playlist_conversion_still_obeys_the_play_downgrade():
    """No "play" word, so "now" is downgraded first and the conversion keeps
    the downgraded placement."""
    got = enforce_intent(
        [Action("play", query="chill", placement="now")], "the chill playlist"
    )
    assert got == [Action("playlist", name="chill", placement="end")]


def test_other_actions_pass_through_untouched():
    """Verbs that cannot replace the current track are never rewritten.

    `playlist` is deliberately NOT in this list: with placement "now" it is a
    second interrupting action (the dispatcher skips the current track), so it
    is subject to the same downgrade as `play` — see the tests below. This
    test now protects only the genuinely inert verbs.
    """
    actions = [
        Action("skip", count=3),
        Action("volume", level=50),
        Action("playlist", name="chill", placement="end"),
        Action("playlist", name="chill", placement="next"),
        Action("now_playing"),
    ]
    assert enforce_intent(actions, "whatever was said") == actions


def test_a_playlist_cannot_interrupt_without_an_explicit_play():
    """The rule is about INTERRUPTION, not about the verb.

    `playlist` with placement "now" makes voice_control skip the playing
    track, so a model returning it for a bare noun phrase ("my chill
    playlist") would drop the current song with no "play" ever spoken.
    """
    got = enforce_intent(
        [Action("playlist", name="chill", placement="now")], "my chill playlist"
    )
    assert got == [Action("playlist", name="chill", placement="end")]


@pytest.mark.parametrize(
    ("language", "said"),
    [
        ("en", "play bohemian rhapsody"),
        ("ko", "보헤미안 랩소디 재생해줘"),
        ("ko", "보헤미안 랩소디 틀어줘"),
        ("ja", "ボヘミアンラプソディを再生して"),
        ("ja", "ボヘミアンラプソディをかけて"),
        ("es", "reproduce bohemian rhapsody"),
        ("es", "pon bohemian rhapsody"),
        ("fr", "joue bohemian rhapsody"),
        ("fr", "mets bohemian rhapsody"),
        ("de", "spiele bohemian rhapsody"),
        ("zh", "播放波西米亚狂想曲"),
    ],
)
def test_now_survives_a_play_verb_in_every_supported_language(language, said):
    """The downgrade must not silently cripple non-English speakers: without
    a per-language verb table a Korean user could only ever APPEND, never
    replace, while the key still reported success."""
    got = enforce_intent(
        [Action("play", query="bohemian rhapsody", placement="now")],
        said,
        language,
    )
    assert got[0].placement == "now"


@pytest.mark.parametrize(
    ("language", "said"),
    [
        ("en", "bohemian rhapsody"),
        ("ko", "보헤미안 랩소디"),
        ("ja", "ボヘミアンラプソディ"),
        ("es", "bohemian rhapsody"),
        ("fr", "bohemian rhapsody"),
        ("de", "bohemian rhapsody"),
        ("zh", "波西米亚狂想曲"),
    ],
)
def test_a_bare_noun_phrase_is_still_downgraded_in_every_language(language, said):
    got = enforce_intent(
        [Action("play", query="bohemian rhapsody", placement="now")],
        said,
        language,
    )
    assert got[0].placement == "end"


def test_an_english_play_does_not_authorise_an_interrupt_in_another_language():
    """The table is consulted per request, not unioned: a language's verbs
    are the only ones that count for that language."""
    got = enforce_intent(
        [Action("play", query="x", placement="now")], "play x", "ko"
    )
    assert got[0].placement == "end"


def test_latin_verbs_are_whole_words_not_substrings():
    """Substring matching would let the Spanish "ponés" (you put) authorise
    an interrupt via the verb "pon"."""
    got = enforce_intent(
        [Action("play", query="x", placement="now")], "ponés la mesa", "es"
    )
    assert got[0].placement == "end"


def test_an_unknown_language_never_downgrades():
    """Degrading to "no verbs known" would cripple that language exactly the
    way this fix exists to prevent; degrade toward obeying the model."""
    got = enforce_intent(
        [Action("play", query="x", placement="now")], "spela x", "sv"
    )
    assert got[0].placement == "now"


def test_language_defaults_to_english():
    assert enforce_intent(
        [Action("play", query="x", placement="now")], "bohemian rhapsody"
    )[0].placement == "end"


def test_a_playlist_may_interrupt_when_the_transcript_said_play():
    got = enforce_intent(
        [Action("playlist", name="chill", placement="now")], "play playlist chill"
    )
    assert got == [Action("playlist", name="chill", placement="now")]
