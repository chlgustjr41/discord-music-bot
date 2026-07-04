"""Failure classification: error signatures -> playbook IDs (RUNBOOK F1-F9).

This is the guardian's brain and is exhaustively tested. Signatures come
from youtube-source plugin releases and past production incidents; when a
new failure shape appears, add its string here and a runbook entry with it.
"""

# Each entry: (playbook_id, lowercase substrings — ANY match wins).
# Order matters: first match is returned, most specific first.
_SIGNATURES: list[tuple[str, list[str]]] = [
    # F2 — OAuth refresh token revoked/expired. The historical multi-day outage.
    ("F2", [
        "requires login",
        "please sign in",
        "oauth",
        "invalid_grant",
        "token was revoked",
        "authentication",
    ]),
    # F1 — poToken stale/rejected (YouTube bot-detection wall).
    ("F1", [
        "sign in to confirm",
        "not a bot",
        "bot detection",
        "potoken",
        "this content isn't available",  # datacenter-IP wall message
        "response was blocked",
    ]),
    # F3 — player JS changed under the plugin (signature/cipher breakage).
    ("F3", [
        "cipher",
        "signature",
        "decipher",
        "must find action functions",
        "could not extract",
        "unable to extract",
        "parsing",
    ]),
]


def classify_canary_error(error_text: str | None) -> str:
    """Map a canary failure's error text to a playbook ID.

    Returns "F1"/"F2"/"F3" on a signature match, "UNKNOWN" otherwise.
    Connectivity failures (timeouts, refused) are F4 but are detected by the
    probe layer, not by error text — they never reach this function.
    """
    if not error_text:
        return "UNKNOWN"
    lowered = error_text.lower()
    for playbook_id, needles in _SIGNATURES:
        if any(needle in lowered for needle in needles):
            return playbook_id
    return "UNKNOWN"
