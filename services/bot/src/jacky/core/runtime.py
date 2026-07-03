"""Process lifecycle: block until SIGTERM/SIGINT so containers stop cleanly."""

import asyncio
import signal


async def wait_for_shutdown(stop: asyncio.Event | None = None) -> None:
    """Block until SIGTERM/SIGINT (or the injected stop event, for tests).

    Call at most once per process: handler registration is process-global,
    and a second concurrent call would displace the first caller's handlers.
    """
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    fallback_handlers: dict[int, object] = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows dev machines: no loop signal handlers; sync handler suffices.
            fallback_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda *_: stop.set())
    try:
        await stop.wait()
    finally:
        for sig, previous in fallback_handlers.items():
            signal.signal(sig, previous)
