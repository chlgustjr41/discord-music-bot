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
        self._frozen_strikes: dict[str, int] = {}
        self._active_failures: set[str] = set()

    async def tick(self) -> None:
        await self._check_canary()
        await self._check_bot()

    async def _check_canary(self) -> None:
        canary = await probe_canary(
            self.session,
            self.settings.lavalink_url,
            self.settings.lavalink_password,
            self.settings.canary_query,
        )
        if canary.ok:
            for playbook_id in sorted(self._active_failures):
                await self.alerter.resolved(playbook_id)
            self._active_failures.clear()
            return

        if not canary.reachable:
            log.warning("canary: lavalink unreachable: %s", canary.error)
            self._active_failures.add("F4")
            outcome = await self.actor.restart("lavalink")
            await self.alerter.alert("F4", f"{canary.error} (restart: {outcome})")
            return

        playbook_id = classify_canary_error(canary.error)
        log.warning("canary failing [%s]: %s", playbook_id, canary.error)
        self._active_failures.add(playbook_id)
        if playbook_id == "F1":
            # token-minter mints immediately at startup: restart == fresh mint.
            outcome = await self.actor.restart("token-minter")
            await self.alerter.alert("F1", f"{canary.error} (minter restart: {outcome})")
        else:
            await self.alerter.alert(playbook_id, canary.error or "")

    async def _check_bot(self) -> None:
        bot = await probe_bot(self.session, self.settings.bot_health_url)
        if not bot.ok:
            self._bot_strikes += 1
            log.warning("bot health failing (strike %d)", self._bot_strikes)
            if self._bot_strikes >= STRIKE_THRESHOLD:
                self._bot_strikes = 0
                outcome = await self.actor.restart("bot")
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
            await self.alerter.alert("F6", f"restart: {outcome}")
        self._prev_players = bot.players
