import asyncio
from dataclasses import dataclass

from aiohttp import web

from minter.mint import run
from tests.test_mint import serve  # reuse the fixture  # noqa: F401


@dataclass(frozen=True)
class FakeSettings:
    pot_provider_url: str
    lavalink_url: str
    lavalink_password: str
    tokens_file: str
    refresh_hours: float


async def test_run_mints_immediately_then_waits_and_stops(serve, tmp_path) -> None:  # noqa: F811
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
    await asyncio.sleep(0.3)
    assert mints == 1  # immediate mint, then sleeping out the 999h interval
    assert (tmp_path / "tokens.env").exists()
    stop.set()
    await asyncio.wait_for(task, timeout=2.0)
