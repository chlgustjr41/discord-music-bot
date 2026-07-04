"""Environment-driven settings. Fail fast on missing required vars."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    pot_provider_url: str
    lavalink_url: str
    lavalink_password: str
    tokens_file: str
    refresh_hours: float

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            pot_provider_url=os.environ["POT_PROVIDER_URL"].rstrip("/"),
            lavalink_url=f"http://{os.environ['LAVALINK_HOST']}:{os.environ['LAVALINK_PORT']}",
            lavalink_password=os.environ["LAVALINK_PASSWORD"],
            tokens_file=os.environ.get("TOKENS_FILE", "/data/tokens/tokens.env"),
            # Must stay below pot-provider's TOKEN_TTL (6h) so a fresh token
            # lands before the old one expires.
            refresh_hours=float(os.environ.get("POT_REFRESH_HOURS", "5.5")),
        )
