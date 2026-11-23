import aiohttp
import pytest
from aiohttp import web


@pytest.fixture
async def serve():
    """Start throwaway aiohttp apps; returns (start(routes) -> base_url)."""
    runners: list[web.AppRunner] = []

    async def start(routes) -> str:
        app = web.Application()
        app.add_routes(routes)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        runners.append(runner)
        port = site._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    yield start
    for runner in runners:
        await runner.cleanup()


@pytest.fixture
def base_env(monkeypatch):
    """Required guardian env vars (obviously-fake values), no Docker overrides.

    Returns the monkeypatch so a test can layer its own `setenv` on top.
    """
    monkeypatch.setenv("LAVALINK_HOST", "lavalink")
    monkeypatch.setenv("LAVALINK_PORT", "2333")
    monkeypatch.setenv("LAVALINK_PASSWORD", "pw")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "http://webhook.invalid/hook")
    monkeypatch.delenv("DOCKER_API_URL", raising=False)
    monkeypatch.delenv("DOCKER_SOCKET", raising=False)
    return monkeypatch


@pytest.fixture
async def http_session():
    session = aiohttp.ClientSession()
    yield session
    await session.close()
