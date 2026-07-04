"""Exhaustive classifier coverage — this module is the guardian's brain.

Error strings below are real shapes from youtube-source releases and past
production incidents; each new incident adds a case here first (TDD for ops).
"""

import pytest

from guardian.classify import classify_canary_error

F2_ERRORS = [
    "This video requires login. Please sign in to view this video.",
    "OAuth token exchange failed: invalid_grant",
    "Token was revoked by the authorization server",
    "Authentication failed for TV client",
    "this url requires oauth but no refresh token is configured",
]

F1_ERRORS = [
    "Sign in to confirm you're not a bot",
    "Bot detection triggered for WEB client",
    "poToken rejected by streaming endpoint",
    "This content isn't available, try again later.",
    "The following content is not available on this app. Response was blocked.",
]

F3_ERRORS = [
    "Must find action functions from script",
    "Cannot decipher signature without cipher script",
    "Could not extract signature deciphering functions",
    "Unable to extract player version from script",
    "cipher signature extraction failed",
    "Error parsing player script variables",
]

UNKNOWN_ERRORS = [
    "Something went wrong",
    "java.lang.NullPointerException",
    "read timed out on upstream",
    "",
]


@pytest.mark.parametrize("error", F2_ERRORS)
def test_oauth_failures_classify_f2(error):
    assert classify_canary_error(error) == "F2"


@pytest.mark.parametrize("error", F1_ERRORS)
def test_bot_detection_classifies_f1(error):
    assert classify_canary_error(error) == "F1"


@pytest.mark.parametrize("error", F3_ERRORS)
def test_plugin_breakage_classifies_f3(error):
    assert classify_canary_error(error) == "F3"


@pytest.mark.parametrize("error", UNKNOWN_ERRORS)
def test_unrecognized_errors_are_unknown(error):
    assert classify_canary_error(error) == "UNKNOWN"


def test_none_is_unknown():
    assert classify_canary_error(None) == "UNKNOWN"


def test_classification_is_case_insensitive():
    assert classify_canary_error("PLEASE SIGN IN") == "F2"
    assert classify_canary_error("SIGN IN TO CONFIRM you're not a bot") == "F1"


def test_f2_beats_f1_on_ambiguous_login_text():
    # "please sign in" (login wall) must route to reauth, not to token-minter.
    assert classify_canary_error("Please sign in. Sign in to confirm your account") == "F2"
