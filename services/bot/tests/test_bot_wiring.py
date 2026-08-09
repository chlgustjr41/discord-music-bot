"""setup_hook wiring: the voice feature must not ship dark.

`register_control_routes(interpreter=None)` degrades every voice request to the
fallback grammar parser — silently, and invisibly from the outside. That is
exactly how the LLM interpreter shipped disabled, so it gets a test.

JackyBot.__init__ opens Firebase, so these build the instance through a thin
subclass that skips it and stubs the heavy collaborators setup_hook constructs.
"""

from types import SimpleNamespace
from typing import Any

import pytest

import jacky.api.auth_routes as auth_routes_mod
import jacky.api.control as control_mod
import jacky.core.bot as bot_mod
from jacky.config import Settings

SETTINGS_BASE = dict(
    discord_token="tok",
    lavalink_host="lavalink",
    lavalink_port=2333,
    lavalink_password="pw",
    firebase_credentials="/run/secrets/firebase.json",
    firestore_database="db",
    web_app_url="http://localhost:5173",
    health_port=8080,
    guardian_status_url="http://guardian:8081/status",
    idle_timeout_seconds=300,
    empty_channel_timeout_seconds=120,
    discord_client_id="123",
    discord_client_secret="secret",
    public_control_url="https://control.example.com",
    openai_api_key="",
    openai_stt_model="gpt-4o-mini-transcribe",
    openai_intent_model="gpt-4o-mini",
)


class _Stub:
    """Accepts any construction and any attribute assignment."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def start(self) -> None:
        pass

    def __getattr__(self, name: str) -> Any:
        return _Stub()


class _TestBot(bot_mod.JackyBot):
    # Shadows discord.Client.user, which needs a live connection state.
    user = SimpleNamespace(id=1)

    def __init__(self, settings: Settings) -> None:  # no super(): skips Firebase
        self.settings = settings
        self.repo = _Stub()
        self.http_session = None
        self.node = None
        self.service = None
        self.token_store = None
        self._health_runner = None


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Neutralise setup_hook's heavy collaborators; capture control-route kwargs."""
    calls: dict[str, Any] = {}
    monkeypatch.setattr(bot_mod.aiohttp, "ClientSession", _Stub)
    monkeypatch.setattr(bot_mod, "LavalinkNode", _Stub)
    monkeypatch.setattr(bot_mod, "SingleNodeProvider", _Stub)
    monkeypatch.setattr(bot_mod, "PlayerService", _Stub)
    monkeypatch.setattr(bot_mod, "SummonWatcher", _Stub)
    monkeypatch.setattr(bot_mod, "EXTENSIONS", [])
    monkeypatch.setattr(bot_mod, "build_app", lambda *a, **k: _Stub())

    async def _no_health(*a: Any, **k: Any) -> Any:
        return None

    monkeypatch.setattr(bot_mod, "start_health_server", _no_health)
    monkeypatch.setattr(auth_routes_mod, "register_auth_routes", lambda *a, **k: None)
    monkeypatch.setattr(
        control_mod,
        "register_control_routes",
        lambda *a, **k: calls.update(k),
    )
    return calls


async def _run(settings_overrides: dict[str, Any]) -> None:
    bot = _TestBot(Settings(**{**SETTINGS_BASE, **settings_overrides}))
    await bot.setup_hook()


@pytest.mark.asyncio
async def test_openai_key_wires_a_real_interpreter(captured: dict[str, Any]) -> None:
    await _run({"openai_api_key": "sk-test"})

    assert captured["transcriber"] is not None
    assert captured["voice_dispatcher"] is not None
    interpreter = captured["interpreter"]
    assert interpreter is not None, "voice interpretation would silently fall back"
    assert interpreter.model == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_intent_model_setting_reaches_the_interpreter(
    captured: dict[str, Any],
) -> None:
    await _run({"openai_api_key": "sk-test", "openai_intent_model": "gpt-4o"})
    assert captured["interpreter"].model == "gpt-4o"


@pytest.mark.asyncio
async def test_no_openai_key_leaves_voice_unwired(captured: dict[str, Any]) -> None:
    await _run({"openai_api_key": ""})
    assert captured["interpreter"] is None
    assert captured["transcriber"] is None
