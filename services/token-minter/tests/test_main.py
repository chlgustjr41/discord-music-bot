import asyncio

import pytest

from minter import __main__ as entry


async def test_main_exits_when_minter_loop_crashes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POT_PROVIDER_URL", "http://x:4416")
    monkeypatch.setenv("LAVALINK_HOST", "x")
    monkeypatch.setenv("LAVALINK_PORT", "2333")
    monkeypatch.setenv("LAVALINK_PASSWORD", "pw")

    async def exploding_run(settings, stop) -> None:
        raise OSError("tokens volume is read-only")

    monkeypatch.setattr(entry, "run", exploding_run)
    with pytest.raises(OSError, match="read-only"):
        await asyncio.wait_for(entry.main(), timeout=3.0)
