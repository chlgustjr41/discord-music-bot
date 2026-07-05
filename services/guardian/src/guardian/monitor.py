"""The supervisor loop: probe -> classify -> act -> alert.

Policy (RUNBOOK F1-F9):
- F4 Lavalink unreachable  -> restart lavalink (cooldown), alert
- F1 poToken rejected      -> restart token-minter (mints immediately), alert
- F2/F3/unknown            -> alert with the exact operator command
- F5 bot health failing    -> two strikes, then restart bot, alert
- F6 frozen position       -> two strikes, then restart bot (convergence
                              resumes at position), alert
- recovery                 -> "[Fx resolved]" once the canary passes again
"""

import logging
import time
from collections import deque
from datetime import datetime, timezone

import aiohttp

from guardian.act import Actor
from guardian.alert import Alerter
from guardian.classify import classify_canary_error
from guardian.config import Settings
from guardian.probe import frozen_guilds, probe_bot, probe_canary

log = logging.getLogger("guardian.monitor")

STRIKE_THRESHOLD = 2  # consecutive probes before restarting (avoids blips)


class Guardian:
    def __init__(
        self,
        settings: Settings,
        session: aiohttp.ClientSession,
        actor: Actor,
        alerter: Alerter,
    ) -> None:
        self.settings = settings
        self.session = session
        self.actor = actor
        self.alerter = alerter
        self._prev_players: dict = {}
        self._bot_strikes = 0
        self._lavalink_strikes = 0
        self._frozen_strikes: dict[str, int] = {}
        self._active_failures: set[str] = set()
        # Snapshot served by the /status endpoint (read from Discord via j!status).
        self.started_at = time.monotonic()
        self.status: dict = {"canary": None, "bot": None, "lastProbeAt": None}
        self.actions: deque = deque(maxlen=10)

    def _record_action(self, playbook_id: str, action: str) -> None:
        self.actions.append({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "playbook": playbook_id,
            "action": action,
        })

    async def tick(self) -> None:
        await self._check_canary()
        await self._check_bot()
        self.status["lastProbeAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.status["activeFailures"] = sorted(self._active_failures)
        self.status["uptimeSeconds"] = int(time.monotonic() - self.started_at)

    async def _check_canary(self) -> None:
        canary = await probe_canary(
            self.session,
            self.settings.lavalink_url,
            self.settings.lavalink_password,
            self.settings.canary_query,
        )
        if canary.ok:
            self.status["canary"] = {"ok": True}
            self._lavalink_strikes = 0
            for playbook_id in sorted(self._active_failures):
                await self.alerter.resolved(playbook_id)
            self._active_failures.clear()
            return

        if not canary.reachable:
            # Two strikes before restarting: the guardian probes immediately on
            # start, and a one-strike F4 restart-kills a Lavalink that is merely
            # still booting after a stack (re)start — observed live 2026-07-05.
            self._lavalink_strikes += 1
            log.warning(
                "canary: lavalink unreachable (strike %d): %s",
                self._lavalink_strikes, canary.error,
            )
            self.status["canary"] = {"ok": False, "playbook": "F4", "error": canary.error}
            if self._lavalink_strikes >= STRIKE_THRESHOLD:
                self._lavalink_strikes = 0
                self._active_failures.add("F4")
                outcome = await self.actor.restart("lavalink")
                self._record_action("F4", f"restart lavalink: {outcome}")
                await self.alerter.alert("F4", f"{canary.error} (restart: {outcome})")
            return
        self._lavalink_strikes = 0

        playbook_id = classify_canary_error(canary.error)
        log.warning("canary failing [%s]: %s", playbook_id, canary.error)
        self.status["canary"] = {"ok": False, "playbook": playbook_id, "error": canary.error}
        self._active_failures.add(playbook_id)
        if playbook_id == "F1":
            # token-minter mints immediately at startup: restart == fresh mint.
            outcome = await self.actor.restart("token-minter")
            self._record_action("F1", f"restart token-minter: {outcome}")
            await self.alerter.alert("F1", f"{canary.error} (minter restart: {outcome})")
        else:
            await self.alerter.alert(playbook_id, canary.error or "")

    async def _check_bot(self) -> None:
        bot = await probe_bot(self.session, self.settings.bot_health_url)
        self.status["bot"] = {"ok": bot.ok, "players": len(bot.players)}
        if not bot.ok:
            self._bot_strikes += 1
            log.warning("bot health failing (strike %d)", self._bot_strikes)
            if self._bot_strikes >= STRIKE_THRESHOLD:
                self._bot_strikes = 0
                outcome = await self.actor.restart("bot")
                self._record_action("F5", f"restart bot: {outcome}")
                await self.alerter.alert("F5", f"restart: {outcome}")
            self._prev_players = {}
            return
        self._bot_strikes = 0

        frozen = set(frozen_guilds(self._prev_players, bot.players))
        for gid in list(self._frozen_strikes):
            if gid not in frozen:
                del self._frozen_strikes[gid]
        restart_needed = False
        for gid in frozen:
            strikes = self._frozen_strikes.get(gid, 0) + 1
            self._frozen_strikes[gid] = strikes
            if strikes >= STRIKE_THRESHOLD:
                restart_needed = True
        if restart_needed:
            log.warning("frozen playback confirmed in guilds %s — restarting bot",
                        sorted(self._frozen_strikes))
            self._frozen_strikes.clear()
            outcome = await self.actor.restart("bot")
            self._record_action("F6", f"restart bot (frozen playback): {outcome}")
            await self.alerter.alert("F6", f"restart: {outcome}")
        self._prev_players = bot.players


async def start_status_server(guardian: Guardian, port: int) -> "aiohttp.web.AppRunner":
    """GET /status — the guardian's view, consumed by the bot's j!status."""
    from aiohttp import web

    async def status(_request: web.Request) -> web.Response:
        return web.json_response({**guardian.status, "actions": list(guardian.actions)})

    app = web.Application()
    app.add_routes([web.get("/status", status)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("status endpoint listening on :%d", port)
    return runner
