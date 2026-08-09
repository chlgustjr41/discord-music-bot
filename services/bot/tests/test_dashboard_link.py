"""Pure URL builders. Shared so the Dashboard key and the voice command
cannot drift apart."""

from jacky.api.dashboard_link import entry_url, session_url


def test_session_url_joins_cleanly():
    assert session_url("https://x.dev", "CODE1234") == "https://x.dev/dashboard/CODE1234"


def test_trailing_slashes_do_not_double_up():
    assert session_url("https://x.dev/", "C1") == "https://x.dev/dashboard/C1"
    assert session_url("https://x.dev///", "C1") == "https://x.dev/dashboard/C1"


def test_entry_url_is_the_no_session_destination():
    assert entry_url("https://x.dev/") == "https://x.dev/app"
