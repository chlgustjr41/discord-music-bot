import asyncio
import logging

from jacky import __version__
from jacky.core.runtime import wait_for_shutdown

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("jacky")


async def main() -> None:
    log.info("jacky-bot %s started (M1 scaffold; playback lands in M3)", __version__)
    await wait_for_shutdown()
    log.info("shutdown signal received; exiting cleanly")


if __name__ == "__main__":
    asyncio.run(main())
