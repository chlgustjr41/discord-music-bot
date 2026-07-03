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
    loop_managed: list[int] = []
    previous: dict[int, object] = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous[sig] = signal.getsignal(sig)
        try:
            loop.add_signal_handler(sig, stop.set)
            loop_managed.append(sig)
        except NotImplementedError:
            # Windows dev machines: no loop signal handlers; sync handler suffices.
            signal.signal(sig, lambda *_: stop.set())
    try:
        await stop.wait()
    finally:
        # Both registration paths mutate the process-global disposition
        # (add_signal_handler installs its own C-level handler), so both
        # must be undone or callers inherit handlers aimed at a dead event.
        for sig in loop_managed:
            loop.remove_signal_handler(sig)
        for sig, prev in previous.items():
            signal.signal(sig, prev)
