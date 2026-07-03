"""Process lifecycle: block until SIGTERM/SIGINT so containers stop cleanly."""

import asyncio
import signal


async def wait_for_shutdown(stop: asyncio.Event | None = None) -> None:
    """Block until SIGTERM/SIGINT (or the injected stop event, for tests)."""
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows dev machines: no loop signal handlers; sync handler suffices.
            signal.signal(sig, lambda *_: stop.set())
    await stop.wait()
