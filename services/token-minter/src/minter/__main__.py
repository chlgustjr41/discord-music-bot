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
    minter_task = asyncio.create_task(run(settings, stop))
    shutdown_task = asyncio.create_task(wait_for_shutdown(stop=stop))
    done, _ = await asyncio.wait({minter_task, shutdown_task}, return_when=asyncio.FIRST_COMPLETED)
    if minter_task in done and not stop.is_set():
        # The loop died on its own: unblock the signal waiter (restores
        # handlers), then propagate so the process exits nonzero and
        # compose restarts us — a dead loop must never look healthy.
        stop.set()
        await shutdown_task
        minter_task.result()
        raise RuntimeError("minter loop exited without a shutdown signal")
    await shutdown_task
    await minter_task
    log.info("shutdown signal received; exiting cleanly")


if __name__ == "__main__":
    asyncio.run(main())
