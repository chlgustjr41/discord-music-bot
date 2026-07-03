import asyncio

from guardian.core.runtime import wait_for_shutdown


async def test_returns_when_stop_event_set() -> None:
    stop = asyncio.Event()

    async def trigger() -> None:
        await asyncio.sleep(0.01)
        stop.set()

    asyncio.get_running_loop().create_task(trigger())
    await asyncio.wait_for(wait_for_shutdown(stop=stop), timeout=1.0)
