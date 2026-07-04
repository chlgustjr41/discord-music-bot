import asyncio
import logging

from jacky import __version__
from jacky.config import Settings
from jacky.core.bot import JackyBot
from jacky.core.runtime import wait_for_shutdown

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("jacky")


async def main() -> None:
    settings = Settings.from_env()
    log.info("jacky-bot %s starting", __version__)
    bot = JackyBot(settings)
    bot_task = asyncio.get_running_loop().create_task(bot.start(settings.discord_token))
    stop = asyncio.Event()
    shutdown_task = asyncio.get_running_loop().create_task(wait_for_shutdown(stop=stop))
    # Crash-only: a fatal bot error exits the process; compose restarts it.
    done, _pending = await asyncio.wait(
        {bot_task, shutdown_task}, return_when=asyncio.FIRST_COMPLETED
    )
    if bot_task in done and bot_task.exception():
        raise bot_task.exception()
    log.info("shutdown signal received; closing")
    stop.set()
    await bot.close()
    log.info("exiting cleanly")


if __name__ == "__main__":
    asyncio.run(main())
