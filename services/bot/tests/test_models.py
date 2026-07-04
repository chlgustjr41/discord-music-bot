from jacky.audio.models import LoadResult, is_url, to_identifier, to_track_data
from tests.conftest import make_track


def test_to_identifier_wraps_plain_queries():
    assert to_identifier("never gonna give you up") == "ytsearch:never gonna give you up"
    assert to_identifier("https://youtu.be/x") == "https://youtu.be/x"
    assert is_url("http://a.b") and not is_url("hello http://a.b")


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
