"""Daily youtube-source release watcher: warn BEFORE breakage (playbook F3).

YouTube changes its player JS every ~1-2 weeks; the plugin ships fixes fast.
Alerting on a new upstream release usually precedes the canary failing.
"""

import logging

import aiohttp

log = logging.getLogger("guardian.watcher")

RELEASES_URL = "https://api.github.com/repos/lavalink-devs/youtube-source/releases/latest"


async def latest_plugin_version(session: aiohttp.ClientSession) -> str | None:
    try:
        async with session.get(
            RELEASES_URL, headers={"Accept": "application/vnd.github+json"}
        ) as resp:
            if resp.status != 200:
                log.warning("release check -> HTTP %s", resp.status)
                return None
            body = await resp.json(content_type=None)
    except Exception as exc:  # noqa: BLE001 — the watcher is advisory
        log.warning("release check failed: %s", exc)
        return None
    tag = body.get("tag_name", "")
    return tag.lstrip("v") or None


class ReleaseWatcher:
    def __init__(self, session: aiohttp.ClientSession, alerter, pinned_version: str) -> None:
        self._session = session
        self._alerter = alerter
        self._pinned = pinned_version
        self._notified: str | None = None

    async def check(self) -> None:
        if not self._pinned:
            return
        latest = await latest_plugin_version(self._session)
        if latest and latest != self._pinned and latest != self._notified:
            self._notified = latest
            await self._alerter.info(
                f"📦 youtube-source **{latest}** is out (running {self._pinned}). "
                f"Bump `YOUTUBE_PLUGIN_VERSION` in deploy/.env and `make up` at the "
                f"next opportunity — stale plugins are the F3 failure class."
            )
