"""Environment-driven settings. Fail fast on missing required vars (crash-only)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    discord_token: str
    lavalink_host: str
    lavalink_port: int
    lavalink_password: str
    firebase_credentials: str
    firestore_database: str
    web_app_url: str
    health_port: int
    guardian_status_url: str
    idle_timeout_seconds: int
    empty_channel_timeout_seconds: int
    # voice control (dormant unless enabled)
    voice_control_enabled: bool
    voice_listener_url: str
    voice_internal_token: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            discord_token=os.environ["DISCORD_TOKEN"],
            lavalink_host=os.environ["LAVALINK_HOST"],
            lavalink_port=int(os.environ["LAVALINK_PORT"]),
            lavalink_password=os.environ["LAVALINK_PASSWORD"],
            firebase_credentials=os.environ["FIREBASE_SERVICE_ACCOUNT_KEY"],
            firestore_database=os.environ.get("FIRESTORE_DATABASE", "discord-music-bot"),
            web_app_url=os.environ.get("WEB_APP_URL", "http://localhost:5173").rstrip("/"),
            health_port=int(os.environ.get("HEALTH_PORT", "8080")),
            guardian_status_url=os.environ.get(
                "GUARDIAN_STATUS_URL", "http://guardian:8081/status"
            ),
            idle_timeout_seconds=int(os.environ.get("IDLE_TIMEOUT_SECONDS", "300")),
            empty_channel_timeout_seconds=int(
                os.environ.get("EMPTY_CHANNEL_TIMEOUT_SECONDS", "120")
            ),
            voice_control_enabled=os.environ.get("VOICE_CONTROL_ENABLED", "")
                .lower() in ("1", "true", "yes"),
            voice_listener_url=os.environ.get(
                "VOICE_LISTENER_URL", "http://voice-listener:8090"
            ),
            voice_internal_token=os.environ.get("VOICE_INTERNAL_TOKEN", ""),
        )

    @property
    def lavalink_url(self) -> str:
        return f"http://{self.lavalink_host}:{self.lavalink_port}"
