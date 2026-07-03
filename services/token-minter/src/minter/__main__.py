import asyncio
import logging

from minter import __version__
from minter.config import Settings
from minter.core.runtime import wait_for_shutdown
from minter.mint import run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("minter")


async def main() -> None:
    settings = Settings.from_env()
    log.info("token-minter %s started (refresh every %sh)", __version__, settings.refresh_hours)
    stop = asyncio.Event()
    minter_task = asyncio.get_running_loop().create_task(run(settings, stop))
    await wait_for_shutdown(stop=stop)
    await minter_task
    log.info("shutdown signal received; exiting cleanly")


if __name__ == "__main__":
    asyncio.run(main())
