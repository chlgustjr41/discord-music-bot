"""Mapping between Lavalink v4 track objects and the Firestore track schema.

The Firestore schema (title/artist/url/thumbnail/duration/requestedBy) is the
contract with the web dashboard — it predates the rewrite and must not change.
Lavalink `encoded` blobs are never persisted: they are node-version specific,
so tracks are re-resolved from their URL at play time (crash-only, ADR-0003).
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# An RFC 3986 scheme followed by "//". Deliberately wider than http(s):
# ftp://, file:// and the rest are just as much "a URL" to whichever Lavalink
# source claims them, and classing them as text would send them on as a search.
_URL_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)

# The ONLY hosts whose links may be handed to Lavalink, compared as whole
# hostnames. Lavalink fetches what it is given from inside the network it
# runs in, so this set is what stops a queued link from reaching devices on
# the LAN, or from disclosing the home IP to a server the sender chose.
#
# MUST stay in step with the sources enabled in
# services/lavalink/application.yml.tmpl (YouTube through youtube-plugin, plus
# SoundCloud). A host listed here whose source is off merely fails to load; a
# source switched on there without its hosts here cannot be reached by link.
ALLOWED_URL_HOSTS = frozenset({
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "soundcloud.com",
    "www.soundcloud.com",
    "m.soundcloud.com",
    "on.soundcloud.com",
})

_DEFAULT_PORTS = {"http": 80, "https": 443}

# Said wherever a refused link has a person waiting on the answer (j!play's
# reply, the voice key's detail). Distinct from "no results" on purpose: that
# one reads as "try again", and trying again cannot help.
UNSUPPORTED_LINK_MESSAGE = "Unsupported link — only YouTube and SoundCloud links work"


def is_url(query: object) -> bool:
    """URL-LOOKING: starts with `scheme://`. Says nothing about whether it
    may be loaded — that is supported_url. Never raises: queue items and
    search queries are written by web clients, so a non-string is possible."""
    return isinstance(query, str) and bool(_URL_SCHEME_RE.match(query.strip()))


def supported_url(query: object) -> str | None:
    """The form of an allowlisted link that may be handed to Lavalink, or
    None for every other value.

    The result is rebuilt from the validated scheme and hostname rather than
    passed through, so what Lavalink parses is what was checked here:
    lower-cased, no credentials, no port, no trailing dot. Everything after
    the host is copied byte-for-byte.
    """
    if not is_url(query):
        return None
    url = query.strip()
    # urlsplit silently DELETES tab/CR/LF, and URL parsers disagree about
    # spaces and control characters. No real link needs any of them, so
    # refuse rather than guess which host the next parser would see. It also
    # keeps the slice at the bottom honest: nothing was removed, so the
    # parsed scheme and netloc are still a literal prefix of `url`.
    if any(ch.isspace() or ch < " " or ch == "\x7f" for ch in url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:  # unbalanced "[", or a port that is not 0-65535
        return None
    default_port = _DEFAULT_PORTS.get(parts.scheme)
    if default_port is None:
        return None
    # Refused, not stripped: "name@host" is in a link like this to make one
    # host read as another, and picking the half that was "meant" is exactly
    # the guess an allowlist exists to avoid.
    if "@" in parts.netloc:
        return None
    if port not in (None, default_port):
        return None
    # Exact membership — never endswith/in — so "youtube.com.evil.test" and
    # "notyoutube.com" are simply different names. One trailing dot is the
    # same DNS name written fully qualified.
    host = (parts.hostname or "").removesuffix(".")
    if host not in ALLOWED_URL_HOSTS:
        return None
    rest = url[len(parts.scheme) + len("://") + len(parts.netloc):]
    return f"{parts.scheme}://{host}{rest}"


def to_identifier(query: object) -> str | None:
    """What to ask Lavalink for, given a raw query — or None for "nothing".

    Plain text becomes a ytsearch, as it always has. Anything URL-looking is
    either an allowlisted link (in supported_url's form) or None. Never a
    search for the link: that would still carry it to Lavalink, and would
    answer a refused link by quietly playing whatever the search turned up.
    """
    if not isinstance(query, str):
        return None
    if is_url(query):
        return supported_url(query)
    return f"ytsearch:{query}"


def to_track_data(track: dict, requested_by: str = "") -> dict:
    """Lavalink v4 track object -> Firestore track document."""
    info = track.get("info", {})
    identifier = info.get("identifier") or ""
    thumbnail = info.get("artworkUrl") or ""
    if not thumbnail and identifier:
        thumbnail = f"https://img.youtube.com/vi/{identifier}/mqdefault.jpg"
    length_ms = info.get("length") or 0
    return {
        "title": info.get("title") or "Unknown",
        "artist": info.get("author") or "",
        "url": info.get("uri") or "",
        "thumbnail": thumbnail,
        "duration": length_ms // 1000,
        "requestedBy": requested_by,
    }


@dataclass(frozen=True)
class LoadResult:
    """Normalized result of a /v4/loadtracks call.

    "unsupported" is the one kind Lavalink never produces: it means the query
    was a link outside ALLOWED_URL_HOSTS and no call was made at all. It
    carries no tracks, so callers that only check `tracks` treat it as "no
    results"; those with a person to answer check `unsupported` first.
    """

    kind: str  # "track" | "playlist" | "search" | "empty" | "error" | "unsupported"
    tracks: list = field(default_factory=list)  # raw Lavalink track objects
    playlist_name: str | None = None
    error: str | None = None
    selected_index: int = -1  # playlist's selectedTrack (the video the URL pointed at)

    @property
    def unsupported(self) -> bool:
        return self.kind == "unsupported"

    @property
    def first(self) -> dict | None:
        return self.tracks[0] if self.tracks else None

    @property
    def tracks_selected_first(self) -> list:
        """Playlist tracks reordered so the URL's own video leads (FUTURE #4)."""
        i = self.selected_index
        if 0 < i < len(self.tracks):
            return [self.tracks[i]] + self.tracks[:i] + self.tracks[i + 1:]
        return self.tracks

    @classmethod
    def from_response(cls, body: dict) -> "LoadResult":
        kind = body.get("loadType", "empty")
        data = body.get("data")
        if kind == "track":
            return cls(kind="track", tracks=[data])
        if kind == "playlist":
            info = data.get("info") or {}
            return cls(
                kind="playlist",
                tracks=data.get("tracks", []),
                playlist_name=info.get("name"),
                selected_index=info.get("selectedTrack", -1),
            )
        if kind == "search":
            return cls(kind="search", tracks=data or [])
        if kind == "error":
            message = (data or {}).get("message") or "unknown load error"
            return cls(kind="error", error=message)
        return cls(kind="empty")
