"""Collection-level watcher for web-dashboard summon requests (FUTURE #2).

Per-guild ServerDocListeners only exist while a session is live — a summon
is precisely a request to START a session, so it needs its own always-on
watcher over the whole `servers` collection (small: one doc per guild).
Snapshot callbacks arrive on an SDK thread; handlers hop to the bot loop.

Anyone can create a document in that collection under any id, and write any
`summonRequest` into it. This callback therefore runs over strangers' data,
on a thread the SDK owns, for every server at once — so nothing a single
document contains may raise out of it, stall the rest of the snapshot, or be
copied into the log.
"""

import asyncio
import logging
import time

from jacky.state.untrusted import as_snowflake, shape

log = logging.getLogger("jacky.summon")

# The collection listener replays EVERY document on EVERY change to any of
# them, so a junk request that is merely skipped comes round again each time
# and sits there indefinitely. Reported at most this often, with a count.
_REPORT_INTERVAL_S = 60.0


class SummonWatcher:
    def __init__(self, bot, repo, service) -> None:
        self.bot = bot
        self.repo = repo
        self.service = service
        self._unsubscribe = None
        self._in_flight: set[str] = set()
        self._last_report: float | None = None
        self._unreported = 0

    def start(self) -> None:
        self._unsubscribe = self.repo.db.collection("servers").on_snapshot(self._on_snapshot)
        log.info("summon watcher started")

    def stop(self) -> None:
        if self._unsubscribe:
            self._unsubscribe.unsubscribe()
            self._unsubscribe = None

    def _on_snapshot(self, snapshot, changes, read_time) -> None:
        for doc in snapshot:
            try:
                self._consider(doc)
            except Exception as exc:  # noqa: BLE001 — one document must not stop the rest
                # Type only: the message can quote the document's content.
                self._report("a document raised %s", type(exc).__name__)

    def _consider(self, doc) -> None:
        data = doc.to_dict() or {}
        request = data.get("summonRequest")
        if request is None:
            return
        if not isinstance(request, dict):
            self._report("summonRequest was a %s", shape(request))
            return
        raw_channel = request.get("channelId")
        if not raw_channel or doc.id in self._in_flight:
            return
        # Validated BEFORE the id is marked in flight: the conversion used to
        # happen after, so a non-numeric id raised with the marker already
        # set, and that id could never be summoned again.
        guild_id = as_snowflake(doc.id)
        channel_id = as_snowflake(raw_channel)
        if guild_id is None or channel_id is None:
            self._report(
                "document id (%s) / channelId (%s) is not a Discord id",
                shape(doc.id), shape(raw_channel),
            )
            return
        self._in_flight.add(doc.id)
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.service.handle_summon(guild_id, str(channel_id)), self.bot.loop
            )
        except BaseException:
            # The hop itself failed (loop closing at shutdown): nothing will
            # ever run the done-callback that releases the id.
            self._in_flight.discard(doc.id)
            raise
        future.add_done_callback(lambda _f, sid=doc.id: self._in_flight.discard(sid))

    def _report(self, reason: str, *args) -> None:
        """Warn about a skipped document — shapes only, and rate-limited.
        Called only from the SDK's snapshot thread, so no lock is needed."""
        self._unreported += 1
        now = time.monotonic()
        if self._last_report is not None and now - self._last_report < _REPORT_INTERVAL_S:
            return
        skipped, self._unreported, self._last_report = self._unreported, 0, now
        log.warning(
            "summon watcher skipped %d malformed request(s); latest: " + reason,
            skipped, *args,
        )
