"""Environment-driven settings. Fail fast on missing required vars (crash-only)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    discord_token: str
    internal_token: str
    bot_intent_url: str
    api_port: int
    model_path: str
    active_window_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            discord_token=os.environ["DISCORD_EARS_TOKEN"],
            internal_token=os.environ["VOICE_INTERNAL_TOKEN"],
            bot_intent_url=os.environ.get(
                "BOT_INTENT_URL", "http://bot:8080/voice-intent"
            ),
            api_port=int(os.environ.get("EARS_API_PORT", "8090")),
            model_path=os.environ.get("VOSK_MODEL_PATH", "/models/vosk-small-en"),
            active_window_seconds=float(os.environ.get("ACTIVE_WINDOW_SECONDS", "5")),
        )
