import pytest

from jacky.config import Settings

REQUIRED = {
    "DISCORD_TOKEN": "tok",
    "LAVALINK_HOST": "lavalink",
    "LAVALINK_PORT": "2333",
    "LAVALINK_PASSWORD": "pw",
    "FIREBASE_SERVICE_ACCOUNT_KEY": "/run/secrets/firebase.json",
}


def test_from_env_reads_required_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    for key in ("WEB_APP_URL", "HEALTH_PORT", "FIRESTORE_DATABASE"):
        monkeypatch.delenv(key, raising=False)
    s = Settings.from_env()
    assert s.lavalink_url == "http://lavalink:2333"
    assert s.firestore_database == "discord-music-bot"
    assert s.health_port == 8080
    assert s.idle_timeout_seconds == 300
    assert s.web_app_url == "http://localhost:5173"


def test_from_env_missing_required_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in REQUIRED:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(KeyError):
        Settings.from_env()


def test_discord_oauth_settings_default_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    for var, val in REQUIRED.items():
        monkeypatch.setenv(var, val)
    monkeypatch.delenv("DISCORD_CLIENT_ID", raising=False)
    monkeypatch.delenv("DISCORD_CLIENT_SECRET", raising=False)
    s = Settings.from_env()
    assert s.discord_client_id == ""
    assert s.discord_client_secret == ""

    monkeypatch.setenv("DISCORD_CLIENT_ID", "123456789012345678")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "secret123")
    s = Settings.from_env()
    assert s.discord_client_id == "123456789012345678"
    assert s.discord_client_secret == "secret123"
