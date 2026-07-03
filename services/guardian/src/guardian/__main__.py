import asyncio
import logging

from guardian import __version__
from guardian.core.runtime import wait_for_shutdown

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("guardian")


async def main() -> None:
    log.info("guardian %s started (M1 scaffold; probes land in M4)", __version__)
    await wait_for_shutdown()
    log.info("shutdown signal received; exiting cleanly")


if __name__ == "__main__":
    asyncio.run(main())
