import re
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

SPOTIFY_URL_PATTERN = re.compile(
    r"https?://open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)"
)

sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
))


def is_spotify_url(query: str) -> bool:
    return bool(SPOTIFY_URL_PATTERN.match(query))


def resolve_spotify_url(url: str) -> list[dict]:
    """Resolve a Spotify URL to a list of track dicts with title and artist."""
    match = SPOTIFY_URL_PATTERN.match(url)
    if not match:
        return []

    url_type, spotify_id = match.group(1), match.group(2)

    if url_type == "track":
        track = sp.track(spotify_id)
        return [_track_to_dict(track)]

    elif url_type == "album":
        album = sp.album_tracks(spotify_id)
        return [_track_to_dict(t) for t in album["items"]]

    elif url_type == "playlist":
        results = sp.playlist_tracks(spotify_id)
        tracks = []
        for item in results["items"]:
            if item["track"]:
                tracks.append(_track_to_dict(item["track"]))
        return tracks

    return []


def _track_to_dict(track: dict) -> dict:
    artists = ", ".join(a["name"] for a in track.get("artists", []))
    return {
        "title": track.get("name", "Unknown"),
        "artist": artists,
        "searchQuery": f"{track.get('name', '')} {artists}",
        "thumbnail": (track.get("album", {}).get("images", [{}])[0].get("url", "")
                       if "album" in track else ""),
        "duration": track.get("duration_ms", 0) // 1000,
    }
