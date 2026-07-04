"""Collection-level watcher for web-dashboard summon requests (FUTURE #2).

Per-guild ServerDocListeners only exist while a session is live — a summon
is precisely a request to START a session, so it needs its own always-on
watcher over the whole `servers` collection (small: one doc per guild).
Snapshot callbacks arrive on an SDK thread; handlers hop to the bot loop.
"""

import asyncio
import logging

log = logging.getLogger("jacky.summon")


class SummonWatcher:
    def __init__(self, bot, repo, service) -> None:
        self.bot = bot
        self.repo = repo
        self.service = service
        self._unsubscribe = None
        self._in_flight: set[str] = set()

    def start(self) -> None:
        self._unsubscribe = self.repo.db.collection("servers").on_snapshot(self._on_snapshot)
        log.info("summon watcher started")

    def stop(self) -> None:
        if self._unsubscribe:
            self._unsubscribe.unsubscribe()
            self._unsubscribe = None

    def _on_snapshot(self, snapshot, changes, read_time) -> None:
        for doc in snapshot:
            data = doc.to_dict() or {}
            request = data.get("summonRequest")
            channel_id = (request or {}).get("channelId")
            if not channel_id or doc.id in self._in_flight:
                continue
            self._in_flight.add(doc.id)
            future = asyncio.run_coroutine_threadsafe(
                self.service.handle_summon(int(doc.id), str(channel_id)), self.bot.loop
            )
            future.add_done_callback(lambda _f, sid=doc.id: self._in_flight.discard(sid))
