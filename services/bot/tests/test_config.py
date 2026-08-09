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


def test_public_control_url_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    for var, val in REQUIRED.items():
        monkeypatch.setenv(var, val)
    monkeypatch.delenv("PUBLIC_CONTROL_URL", raising=False)
    s = Settings.from_env()
    assert s.public_control_url == "https://control.jacky-music-bot.com"

    monkeypatch.setenv("PUBLIC_CONTROL_URL", "https://ctrl.example.com/")
    s = Settings.from_env()
    assert s.public_control_url == "https://ctrl.example.com"


def test_public_control_url_ignores_an_empty_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """docker-compose passes optional vars as ${VAR:-}, which SETS them to an
    empty string — so a .get() default never fires. An empty base produced the
    relative redirect_uri "/control/auth/callback", which Discord rejects, and
    every sign-in failed. Empty must fall back to the default, like unset.
    """
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    default = "https://control.jacky-music-bot.com"

    monkeypatch.delenv("PUBLIC_CONTROL_URL", raising=False)
    assert Settings.from_env().public_control_url == default

    monkeypatch.setenv("PUBLIC_CONTROL_URL", "")  # what compose actually sends
    assert Settings.from_env().public_control_url == default

    monkeypatch.setenv("PUBLIC_CONTROL_URL", "https://other.example.com/")
    assert Settings.from_env().public_control_url == "https://other.example.com"


def test_openai_settings_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty must behave like unset: compose passes optional vars as ${VAR:-},
    which SETS them to an empty string, so a .get() default never fires."""
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_STT_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_INTENT_MODEL", raising=False)
    s = Settings.from_env()
    assert s.openai_api_key == ""
    assert s.openai_stt_model == "gpt-4o-mini-transcribe"
    assert s.openai_intent_model == "gpt-4o-mini"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_STT_MODEL", "")
    monkeypatch.setenv("OPENAI_INTENT_MODEL", "")  # what compose actually sends
    s = Settings.from_env()
    assert s.openai_api_key == "sk-test"
    assert s.openai_stt_model == "gpt-4o-mini-transcribe"
    assert s.openai_intent_model == "gpt-4o-mini"

    monkeypatch.setenv("OPENAI_STT_MODEL", "whisper-1")
    assert Settings.from_env().openai_stt_model == "whisper-1"

    monkeypatch.setenv("OPENAI_INTENT_MODEL", "gpt-4o")
    assert Settings.from_env().openai_intent_model == "gpt-4o"
