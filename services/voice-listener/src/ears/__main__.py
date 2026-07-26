import asyncio
import logging

from aiohttp import web

from ears.api import build_app
from ears.config import Settings
from ears.gateway import EarsClient

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ears")


async def main() -> None:
    settings = Settings.from_env()
    client = EarsClient(settings)
    runner = web.AppRunner(build_app(client, settings.internal_token))

    async with client:
        await client.login(settings.discord_token)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", settings.api_port).start()
        log.info("ears 0.1.0 started (api :%d)", settings.api_port)
        await client.connect()


if __name__ == "__main__":
    asyncio.run(main())
