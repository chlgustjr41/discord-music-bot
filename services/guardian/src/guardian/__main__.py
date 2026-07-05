import asyncio
import logging

import aiohttp

from guardian import __version__
from guardian.act import Actor, DockerClient
from guardian.alert import Alerter
from guardian.config import Settings
from guardian.core.runtime import wait_for_shutdown
from guardian.monitor import Guardian, start_status_server
from guardian.watcher import ReleaseWatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("guardian")

HEARTBEAT_INTERVAL = 7 * 24 * 3600  # weekly (F9)
RELEASE_CHECK_INTERVAL = 24 * 3600  # daily (F3 early warning)


async def _loop(interval: float, stop: asyncio.Event, fn) -> None:
    while not stop.is_set():
        try:
            await fn()
        except Exception as exc:  # noqa: BLE001 — the supervisor itself must not die
            log.error("loop iteration failed: %s", exc, exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


async def main() -> None:
    settings = Settings.from_env()
    log.info("guardian %s started (probe every %ss)",
             __version__, settings.probe_interval_seconds)

    session = aiohttp.ClientSession()
    docker = DockerClient.for_socket(settings.docker_socket, settings.compose_project)
    actor = Actor(docker)
    alerter = Alerter(session, settings.alert_webhook_url)
    guardian = Guardian(settings, session, actor, alerter)
    watcher = ReleaseWatcher(session, alerter, settings.plugin_version)
    status_runner = await start_status_server(guardian, settings.status_port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    tasks = [
        loop.create_task(_loop(settings.probe_interval_seconds, stop, guardian.tick)),
        loop.create_task(_loop(HEARTBEAT_INTERVAL, stop, alerter.heartbeat)),
        loop.create_task(_loop(RELEASE_CHECK_INTERVAL, stop, watcher.check)),
    ]
    await wait_for_shutdown(stop=stop)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await status_runner.cleanup()
    await docker.close()
    await session.close()
    log.info("shutdown signal received; exiting cleanly")


if __name__ == "__main__":
    asyncio.run(main())
