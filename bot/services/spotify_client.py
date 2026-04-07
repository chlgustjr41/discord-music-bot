import re

SPOTIFY_URL_PATTERN = re.compile(
    r"https?://open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)"
)


def is_spotify_url(query: str) -> bool:
    return bool(SPOTIFY_URL_PATTERN.match(query))
