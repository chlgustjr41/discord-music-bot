import pytest

from minter.config import Settings


def test_from_env_reads_required_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POT_PROVIDER_URL", "http://pot-provider:4416/")
    monkeypatch.setenv("LAVALINK_HOST", "lavalink")
    monkeypatch.setenv("LAVALINK_PORT", "2333")
    monkeypatch.setenv("LAVALINK_PASSWORD", "hunter2")
    monkeypatch.delenv("TOKENS_FILE", raising=False)
    monkeypatch.delenv("POT_REFRESH_HOURS", raising=False)
    s = Settings.from_env()
    assert s.pot_provider_url == "http://pot-provider:4416"  # trailing slash stripped
    assert s.lavalink_url == "http://lavalink:2333"
    assert s.lavalink_password == "hunter2"
    assert s.tokens_file == "/data/tokens/tokens.env"
    # Default keeps a margin below pot-provider's 6h TOKEN_TTL.
    assert s.refresh_hours == 5.5


def test_from_env_missing_required_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POT_PROVIDER_URL", raising=False)
    with pytest.raises(KeyError):
        Settings.from_env()
