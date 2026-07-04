"""Mapping between Lavalink v4 track objects and the Firestore track schema.

The Firestore schema (title/artist/url/thumbnail/duration/requestedBy) is the
contract with the web dashboard — it predates the rewrite and must not change.
Lavalink `encoded` blobs are never persisted: they are node-version specific,
so tracks are re-resolved from their URL at play time (crash-only, ADR-0003).
"""

import re
from dataclasses import dataclass, field

_URL_RE = re.compile(r"^https?://")


def is_url(query: str) -> bool:
    return bool(_URL_RE.match(query))


def to_identifier(query: str) -> str:
    """A raw query becomes a ytsearch; URLs pass through untouched."""
    return query if is_url(query) else f"ytsearch:{query}"


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
    """Normalized result of a /v4/loadtracks call."""

    kind: str  # "track" | "playlist" | "search" | "empty" | "error"
    tracks: list = field(default_factory=list)  # raw Lavalink track objects
    playlist_name: str | None = None
    error: str | None = None

    @property
    def first(self) -> dict | None:
        return self.tracks[0] if self.tracks else None

    @classmethod
    def from_response(cls, body: dict) -> "LoadResult":
        kind = body.get("loadType", "empty")
        data = body.get("data")
        if kind == "track":
            return cls(kind="track", tracks=[data])
        if kind == "playlist":
            return cls(
                kind="playlist",
                tracks=data.get("tracks", []),
                playlist_name=(data.get("info") or {}).get("name"),
            )
        if kind == "search":
            return cls(kind="search", tracks=data or [])
        if kind == "error":
            message = (data or {}).get("message") or "unknown load error"
            return cls(kind="error", error=message)
        return cls(kind="empty")
