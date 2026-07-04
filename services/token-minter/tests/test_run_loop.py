import asyncio
from dataclasses import dataclass

from aiohttp import web

from minter.mint import run


@dataclass(frozen=True)
class FakeSettings:
    pot_provider_url: str
    lavalink_url: str
    lavalink_password: str
    tokens_file: str
    refresh_hours: float


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met before deadline")
        await asyncio.sleep(0.05)


async def test_run_mints_immediately_then_waits_and_stops(serve, tmp_path) -> None:
    mints = 0

    async def get_pot(request: web.Request) -> web.Response:
        nonlocal mints
        mints += 1
        # Response shape pinned by ADR-0004.
        return web.json_response({"contentBinding": "VD", "poToken": "PO"})

    async def youtube(request: web.Request) -> web.Response:
        return web.Response(status=204)

    provider = await serve([web.post("/get_pot", get_pot)])
    lavalink = await serve([web.post("/youtube", youtube)])
    settings = FakeSettings(provider, lavalink, "pw", str(tmp_path / "tokens.env"), 999.0)

    stop = asyncio.Event()
    task = asyncio.get_running_loop().create_task(run(settings, stop))
    await _wait_until(lambda: mints == 1 and (tmp_path / "tokens.env").exists())
    assert mints == 1  # immediate mint, then sleeping out the 999h interval
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)


async def test_run_retries_after_failure_then_recovers(serve, tmp_path) -> None:
    calls = 0

    async def get_pot(request: web.Request) -> web.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return web.json_response({"error": "transient"}, status=500)
        return web.json_response({"contentBinding": "VD", "poToken": "PO"})

    async def youtube(request: web.Request) -> web.Response:
        return web.Response(status=204)

    provider = await serve([web.post("/get_pot", get_pot)])
    lavalink = await serve([web.post("/youtube", youtube)])
    settings = FakeSettings(provider, lavalink, "pw", str(tmp_path / "tokens.env"), 999.0)

    stop = asyncio.Event()
    task = asyncio.get_running_loop().create_task(run(settings, stop, retry_seconds=0.05))
    await _wait_until(lambda: calls >= 2 and (tmp_path / "tokens.env").exists())
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
