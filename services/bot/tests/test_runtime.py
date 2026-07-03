import asyncio

from jacky.core.runtime import wait_for_shutdown


async def test_returns_when_stop_event_set() -> None:
    stop = asyncio.Event()

    async def trigger() -> None:
        await asyncio.sleep(0.01)
        stop.set()

    asyncio.get_running_loop().create_task(trigger())
    await asyncio.wait_for(wait_for_shutdown(stop=stop), timeout=1.0)


async def test_does_not_return_before_signal() -> None:
    stop = asyncio.Event()
    task = asyncio.get_running_loop().create_task(wait_for_shutdown(stop=stop))
    await asyncio.sleep(0.05)
    assert not task.done()
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
