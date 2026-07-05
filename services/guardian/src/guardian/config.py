"""Environment-driven settings. Fail fast on missing required vars (crash-only)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    lavalink_url: str
    lavalink_password: str
    bot_health_url: str
    alert_webhook_url: str
    docker_socket: str
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
            compose_project=os.environ.get("COMPOSE_PROJECT", "jacky-music"),
            probe_interval_seconds=float(os.environ.get("PROBE_INTERVAL_SECONDS", "120")),
            canary_query=os.environ.get(
                "CANARY_QUERY", "ytsearch:rick astley never gonna give you up"
            ),
            plugin_version=os.environ.get("YOUTUBE_PLUGIN_VERSION", ""),
            status_port=int(os.environ.get("STATUS_PORT", "8081")),
        )
