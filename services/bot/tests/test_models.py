import pytest

from jacky.audio.models import (
    ALLOWED_URL_HOSTS,
    LoadResult,
    is_url,
    supported_url,
    to_identifier,
    to_track_data,
)
from tests.conftest import make_track


def test_to_identifier_wraps_plain_queries():
    assert to_identifier("never gonna give you up") == "ytsearch:never gonna give you up"
    assert to_identifier("https://youtu.be/x") == "https://youtu.be/x"
    assert is_url("http://a.b") and not is_url("hello http://a.b")


# ── URL allowlist: what may be handed to Lavalink ────────────────────────
#
# Lavalink fetches whatever URL it is given, from inside the home network.
# These pins are the application-layer guard; each rejected string below is a
# way a looser check (prefix, suffix, substring, regex on the raw string) lets
# an attacker-chosen host through.

REJECTED_URLS = [
    # LAN, link-local metadata, and the audio server's own admin API.
    "http://192.168.1.1/",
    "http://169.254.169.254/latest",
    "http://localhost:2333/v4/info",
    "http://127.0.0.1/",
    "http://[::1]/",
    "http://10.0.0.5:8080/stream.mp3",
    # An allowed name that is not the HOST: userinfo, suffix, query, path.
    "https://youtube.com@evil.test/x",
    "https://youtube.com.evil.test/x",
    "https://evil.test/?u=youtube.com",
    "https://evil.test/youtube.com/watch?v=x",
    "https://evil.test/#https://www.youtube.com/watch?v=x",
    "https://notyoutube.com/watch?v=x",
    "https://youtube.com.:8443/x",
    "https://www.youtube.com../watch?v=x",
    # Userinfo on an otherwise-allowed host: the ONLY thing wrong here is the
    # credentials, so these fail on the userinfo rule and no other.
    "https://user@www.youtube.com/watch?v=x",
    "https://user:pass@youtu.be/x",
    "https://evil.test@www.youtube.com/watch?v=x",
    "https://@www.youtube.com/watch?v=x",
    # Non-default ports, including the other scheme's default.
    "https://www.youtube.com:8443/watch?v=x",
    "http://youtu.be:443/x",
    "https://www.youtube.com:80/watch?v=x",
    "https://www.youtube.com:notaport/watch?v=x",
    "https://www.youtube.com:99999/watch?v=x",
    # Schemes other than http(s) — still URL-looking, so never a search.
    "ftp://youtube.com/x",
    "file:///etc/passwd",
    "javascript://youtube.com/%0aalert(1)",
    "FILE:///etc/passwd",
    "gopher://www.youtube.com/x",
    # Characters URL parsers disagree about.
    "https://www.youtube.com\\@evil.test/x",
    "https://www.youtube.com\t.evil.test/x",
    "https://www.youtube.com/watch?v=x y",
    "https://www.youtube.com/watch?v=x\nhttp://192.168.1.1/",
    "http://[::1",
    "https://",
    "http:///watch?v=x",
]

ACCEPTED_URLS = [
    # (input, what Lavalink is handed)
    ("https://www.youtube.com/watch?v=x", "https://www.youtube.com/watch?v=x"),
    ("https://youtube.com/watch?v=x", "https://youtube.com/watch?v=x"),
    ("https://m.youtube.com/watch?v=x", "https://m.youtube.com/watch?v=x"),
    ("https://music.youtube.com/watch?v=x", "https://music.youtube.com/watch?v=x"),
    ("https://youtu.be/x", "https://youtu.be/x"),
    ("https://soundcloud.com/a/b", "https://soundcloud.com/a/b"),
    ("https://www.soundcloud.com/a/b", "https://www.soundcloud.com/a/b"),
    ("https://m.soundcloud.com/a/b", "https://m.soundcloud.com/a/b"),
    ("https://on.soundcloud.com/AbCd", "https://on.soundcloud.com/AbCd"),
    # Default ports are the same origin; they are dropped on the way out.
    ("https://www.youtube.com:443/watch?v=x", "https://www.youtube.com/watch?v=x"),
    ("http://youtu.be:80/x", "http://youtu.be/x"),
    # Scheme and host are case-insensitive; path and query are NOT touched.
    ("HTTPS://WWW.YOUTUBE.COM/watch?v=x", "https://www.youtube.com/watch?v=x"),
    ("HTTPS://WWW.YOUTUBE.COM/watch?v=AbC", "https://www.youtube.com/watch?v=AbC"),
    # One trailing dot is the same DNS name.
    ("https://www.youtube.com./watch?v=x", "https://www.youtube.com/watch?v=x"),
    # Everything after the host survives byte-for-byte.
    (
        "https://www.youtube.com/playlist?list=PL1&index=2#t=30",
        "https://www.youtube.com/playlist?list=PL1&index=2#t=30",
    ),
    ("https://youtu.be", "https://youtu.be"),
    # A pasted link with stray surrounding whitespace is still that link.
    ("  https://youtu.be/x \n", "https://youtu.be/x"),
]


@pytest.mark.parametrize("url", REJECTED_URLS)
def test_rejected_urls_never_become_an_identifier(url):
    assert is_url(url), "must be recognised as a URL, or it would become a search"
    assert supported_url(url) is None
    # None, not "ytsearch:<url>": the string must not reach Lavalink in ANY
    # form, and a search for it would also quietly play something else.
    assert to_identifier(url) is None


@pytest.mark.parametrize("url,expected", ACCEPTED_URLS)
def test_allowlisted_urls_pass_in_canonical_form(url, expected):
    assert is_url(url)
    assert supported_url(url) == expected
    assert to_identifier(url) == expected


def test_host_check_alone_rejects_a_clean_foreign_url():
    """No userinfo, no port, https — the hostname is the only thing wrong."""
    assert to_identifier("https://evil.test/watch?v=x") is None


def test_userinfo_check_alone_rejects_an_allowed_host():
    """Allowed host, default port, https — the credentials are the only
    thing wrong. Stripping them and loading the rest would be a guess about
    which host the sender meant."""
    assert to_identifier("https://user@www.youtube.com/watch?v=x") is None


def test_allowed_hosts_are_exactly_the_reviewed_set():
    assert ALLOWED_URL_HOSTS == frozenset({
        "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
        "youtu.be",
        "soundcloud.com", "www.soundcloud.com", "m.soundcloud.com",
        "on.soundcloud.com",
    })


@pytest.mark.parametrize("text", [
    "never gonna give you up",
    # A URL in the MIDDLE of text is a search for that text, as it always was.
    "hello http://a.b",
    "that song from https://evil.test/ you know the one",
    "http:/192.168.1.1/",        # not scheme://
    "youtube.com/watch?v=x",     # no scheme at all
    "ytsearch:foo",
    "1http://x",                 # a scheme starts with a letter
    "",
])
def test_plain_text_is_still_a_ytsearch(text):
    assert not is_url(text)
    assert to_identifier(text) == f"ytsearch:{text}"


@pytest.mark.parametrize("value", [None, 5, 3.5, {"a": 1}, ["https://youtu.be/x"]])
def test_non_string_queries_are_unsupported_not_an_exception(value):
    """Queue items and search queries are written by web clients."""
    assert is_url(value) is False
    assert to_identifier(value) is None


def test_to_track_data_maps_and_falls_back_to_yt_thumbnail():
    td = to_track_data(make_track(length_ms=185500), requested_by="Jacob")
    assert td == {
        "title": "Song",
        "artist": "Artist",
        "url": "https://youtu.be/abc123",
        "thumbnail": "https://img.youtube.com/vi/abc123/mqdefault.jpg",
        "duration": 185,
        "requestedBy": "Jacob",
    }


def test_load_result_track_and_playlist_and_error():
    single = LoadResult.from_response({"loadType": "track", "data": make_track()})
    assert single.kind == "track" and single.first["encoded"] == "ENC1"

    playlist = LoadResult.from_response({
        "loadType": "playlist",
        "data": {"info": {"name": "Mix"}, "tracks": [make_track(), make_track()]},
    })
    assert playlist.kind == "playlist"
    assert playlist.playlist_name == "Mix" and len(playlist.tracks) == 2

    search = LoadResult.from_response({"loadType": "search", "data": [make_track()]})
    assert search.kind == "search" and len(search.tracks) == 1

    empty = LoadResult.from_response({"loadType": "empty"})
    assert empty.kind == "empty" and empty.first is None

    err = LoadResult.from_response({"loadType": "error", "data": {"message": "boom"}})
    assert err.kind == "error" and err.error == "boom" and not err.tracks


def test_unsupported_load_result_carries_no_tracks():
    result = LoadResult(kind="unsupported")
    assert result.unsupported and not result.tracks and result.first is None
    assert not LoadResult(kind="empty").unsupported
