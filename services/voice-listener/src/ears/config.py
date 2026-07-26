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
    debug: bool
    debug_capture_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        # These two secrets are compose-injected as ${VAR:-} so the DEFAULT
        # stack boots without them — but the listener only ever runs under the
        # `voice` profile, so an empty value here is a real misconfiguration.
        # Reject it: an empty internal_token would silently turn the shared
        # X-Voice-Token auth into a no-op on both services.
        discord_token = _require_nonempty("DISCORD_EARS_TOKEN")
        internal_token = _require_nonempty("VOICE_INTERNAL_TOKEN")
        return cls(
            discord_token=discord_token,
            internal_token=internal_token,
            bot_intent_url=os.environ.get(
                "BOT_INTENT_URL", "http://bot:8080/voice-intent"
            ),
            api_port=int(os.environ.get("EARS_API_PORT", "8090")),
            model_path=os.environ.get("VOSK_MODEL_PATH", "/models/vosk-small-en"),
            active_window_seconds=float(os.environ.get("ACTIVE_WINDOW_SECONDS", "5")),
            # VOICE_DEBUG turns on per-speaker rx stats, finalization transcript
            # logging, and a bounded WAV capture of decoded audio for offline
            # analysis. Off by default; a diagnostic toggle, not a prod default.
            debug=os.environ.get("VOICE_DEBUG", "").lower() in ("1", "true", "yes"),
            debug_capture_seconds=int(os.environ.get("VOICE_DEBUG_CAPTURE_SECONDS", "20")),
        )


def _require_nonempty(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"{name} must be set to a non-empty value")
    return value
