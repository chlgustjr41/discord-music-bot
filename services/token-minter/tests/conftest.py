import pytest
from aiohttp import web


@pytest.fixture
async def serve():
    """Start throwaway aiohttp servers on ephemeral ports; returns their base URL."""
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
    for r in runners:
        await r.cleanup()
