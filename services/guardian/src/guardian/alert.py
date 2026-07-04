"""Alerts: Discord webhook messages keyed to runbook playbook IDs.

Every alert names its playbook ID and the exact operator command, per the
runbook contract (docs/operations/RUNBOOK.md). A per-playbook cooldown
keeps a persistent failure from flooding the channel; the weekly heartbeat
proves the alert channel itself works (F9).
"""

import logging
import time

import aiohttp

log = logging.getLogger("guardian.alert")

ALERT_COOLDOWN_SECONDS = 1800.0

PLAYBOOK_MESSAGES = {
    "F1": ("🟠 **[F1] poToken rejected** — YouTube bot-detection wall.\n"
           "Automated: token-minter restarted for an immediate fresh mint.\n"
           "If this repeats: `make restart s=token-minter`, then `make logs s=token-minter`."),
    "F2": ("🔴 **[F2] YouTube OAuth token revoked** — all loads failing with "
           "\"requires login\".\n**Run: `make reauth`** (~60s device flow). "
           "poToken (F1 layer) carries most playback meanwhile."),
    "F3": ("🔴 **[F3] youtube-source plugin broken** — YouTube changed its player JS.\n"
           "Check https://github.com/lavalink-devs/youtube-source/releases and bump "
           "`YOUTUBE_PLUGIN_VERSION` in deploy/.env, then `make up`."),
    "F4": ("🟠 **[F4] Lavalink sick/unreachable.**\n"
           "Automated: container restarted; the bot reconnects and resumes from "
           "Firestore. If this repeats: `make logs s=lavalink`."),
    "F5": ("🟠 **[F5] Bot hung (health ping failing).**\n"
           "Automated: bot container restarted; it converges from Firestore."),
    "F6": ("🟠 **[F6] Silent playback (position frozen).**\n"
           "Automated: bot restarted — convergence resumes the track at position. "
           "If this repeats: `make restart s=lavalink`."),
    "UNKNOWN": ("🟡 **[F?] Canary failing with an unrecognized error.** "
                "Check `make logs s=lavalink` and add the signature to "
                "guardian/classify.py + the runbook."),
}


class Alerter:
    def __init__(self, session: aiohttp.ClientSession, webhook_url: str) -> None:
        self._session = session
        self._webhook_url = webhook_url
        self._last_sent: dict[str, float] = {}

    async def _post(self, content: str) -> bool:
        try:
            async with self._session.post(
                self._webhook_url, json={"content": content[:1900]}
            ) as resp:
                return resp.status in (200, 204)
        except Exception as exc:  # noqa: BLE001 — F9: a broken channel must not kill probing
            log.error("webhook post failed: %s", exc)
            return False

    async def alert(self, playbook_id: str, detail: str = "") -> bool:
        """Cooldown-guarded playbook alert. Returns True if actually sent."""
        now = time.monotonic()
        last = self._last_sent.get(playbook_id)
        if last is not None and (now - last) < ALERT_COOLDOWN_SECONDS:
            return False
        self._last_sent[playbook_id] = now
        message = PLAYBOOK_MESSAGES.get(playbook_id, PLAYBOOK_MESSAGES["UNKNOWN"])
        if detail:
            message = f"{message}\n```\n{detail[:600]}\n```"
        message += "\n_Runbook: docs/operations/RUNBOOK.md_"
        return await self._post(message)

    async def resolved(self, playbook_id: str) -> None:
        # Clearing the cooldown lets the next occurrence alert immediately.
        if self._last_sent.pop(playbook_id, None) is not None:
            await self._post(f"✅ **[{playbook_id} resolved]** canary passing again.")

    async def heartbeat(self) -> None:
        await self._post("💓 Guardian heartbeat — alert channel is working. (weekly F9 check)")

    async def info(self, text: str) -> None:
        await self._post(text)
