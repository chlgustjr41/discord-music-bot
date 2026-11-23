"""Environment-driven settings. Fail fast on missing required vars (crash-only)."""

import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

# A DNS name / IPv4 address, or an IPv6 literal (urlsplit lowercases the host).
_HOST = re.compile(r"[a-z0-9._-]+|[0-9a-f:.]+")


def _docker_api_url(raw: str) -> str:
    """Validate DOCKER_API_URL. Returns "" for unset/blank (= use the unix socket).

    Accepted: `http://host[:port]` and nothing more. The endpoint is the
    filtering socket proxy, which speaks plain HTTP and allowlists exact
    request paths — so a path prefix here (e.g. `/v1.43`) would turn every
    restart into a 403, and is rejected at startup instead of at the first
    outage. Errors name the offending part, never the whole value.
    """
    value = raw.strip()
    if not value:
        return ""
    hint = "DOCKER_API_URL must look like http://socket-proxy:2375"
    parts = urlsplit(value)
    if parts.scheme != "http":
        raise ValueError(
            f"{hint}: scheme must be 'http' (plain TCP), got {parts.scheme or 'none'!r}. "
            "Leave it unset to use the unix socket (DOCKER_SOCKET)."
        )
    try:
        port = parts.port  # parsed lazily: this is where a bad port surfaces
    except ValueError:
        raise ValueError(f"{hint}: invalid port") from None
    host = parts.hostname or ""
    if not _HOST.fullmatch(host):
        raise ValueError(f"{hint}: host is empty or not a hostname/IP address")
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"{hint}: credentials in the URL are not supported")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError(
            f"{hint}: only scheme://host[:port] is allowed (no path, query or fragment)"
        )
    if ":" in host:  # IPv6 literal: urlsplit strips the brackets
        host = f"[{host}]"
    return f"http://{host}" if port is None else f"http://{host}:{port}"


@dataclass(frozen=True)
class Settings:
    lavalink_url: str
    lavalink_password: str
    bot_health_url: str
    alert_webhook_url: str
    docker_socket: str
    # Non-empty -> reach Docker over plain TCP at this base URL (the filtering
    # socket proxy) and never open docker_socket. Empty -> unix socket.
    docker_api_url: str
    compose_project: str
    probe_interval_seconds: float
    canary_query: str
    plugin_version: str
    status_port: int

    @classmethod
    def from_env(cls) -> "Settings":
        host = os.environ["LAVALINK_HOST"]
        port = os.environ["LAVALINK_PORT"]
        return cls(
            lavalink_url=f"http://{host}:{port}",
            lavalink_password=os.environ["LAVALINK_PASSWORD"],
            bot_health_url=os.environ.get("BOT_HEALTH_URL", "http://bot:8080/health"),
            alert_webhook_url=os.environ["ALERT_WEBHOOK_URL"],
            docker_socket=os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock"),
            docker_api_url=_docker_api_url(os.environ.get("DOCKER_API_URL", "")),
            compose_project=os.environ.get("COMPOSE_PROJECT", "jacky-music"),
            probe_interval_seconds=float(os.environ.get("PROBE_INTERVAL_SECONDS", "120")),
            canary_query=os.environ.get(
                "CANARY_QUERY", "ytsearch:rick astley never gonna give you up"
            ),
            plugin_version=os.environ.get("YOUTUBE_PLUGIN_VERSION", ""),
            status_port=int(os.environ.get("STATUS_PORT", "8081")),
        )
