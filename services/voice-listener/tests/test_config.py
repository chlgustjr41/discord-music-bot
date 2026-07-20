import pytest

from ears.config import Settings

REQUIRED = {
    "DISCORD_EARS_TOKEN": "tok",
    "VOICE_INTERNAL_TOKEN": "secret",
}


def test_from_env_defaults(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    s = Settings.from_env()
    assert s.bot_intent_url == "http://bot:8080/voice-intent"
    assert s.api_port == 8090
    assert s.model_path == "/models/vosk-small-en"
    assert s.active_window_seconds == 5.0


def test_missing_token_fails_fast(monkeypatch):
    monkeypatch.delenv("DISCORD_EARS_TOKEN", raising=False)
    monkeypatch.setenv("VOICE_INTERNAL_TOKEN", "secret")
    with pytest.raises(KeyError):
        Settings.from_env()
