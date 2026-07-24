"""Voice-control glue: dispatch listener intents onto PlayerService, and
notify the listener when sessions start/stop. Whole module is dormant when
settings.voice_control_enabled is False (nothing constructs it)."""

import asyncio
import logging
from typing import Any

import aiohttp

from jacky.audio.models import to_track_data

log = logging.getLogger("jacky.voice")

VOLUME_STEP = 10


class VoiceIntentDispatcher:
    def __init__(self, service: Any, repo: Any):
        self.service, self.repo = service, repo

    async def dispatch(self, guild_id: int, intent: str, arg: str | None) -> bool:
        sid = str(guild_id)
        if intent == "skip":
            await self.service.skip(guild_id)
        elif intent == "pause":
            await self.service.pause(guild_id, True)
        elif intent == "resume":
            await self.service.pause(guild_id, False)
        elif intent == "stop":
            await self.service.teardown_session(guild_id, clear_queue=True)
        elif intent in ("volume_up", "volume_down"):
            state = await self.repo.get_state(sid) or {}
            current = int(state.get("volume", 80))
            delta = VOLUME_STEP if intent == "volume_up" else -VOLUME_STEP
            await self.service.set_volume(guild_id, current + delta)
        elif intent == "play" and arg:
            return await self._play(guild_id, sid, arg)
        else:
            return False
        return True

    async def _play(self, guild_id: int, sid: str, query: str) -> bool:
        """Mirrors commands/playback.play, minus Discord I/O (voice-requested
        tracks are announced by the usual now-playing flow)."""
        result = await self.service.resolve(query)
        if not result.tracks:
            return False
        td = to_track_data(result.first, "voice command")
        state = await self.repo.get_state(sid) or {}
        if state.get("currentTrack"):
            await self.repo.add_to_queue(sid, td)
            return True
        return bool(await self.service.start_current_track(guild_id, result.first, td))


class ListenerNotifier:
    """Bot -> voice-listener control calls. All failures are soft: voice
    control degrades, music never does."""

    def __init__(self, base_url: str, token: str, repo: Any):
        self.base_url, self.token, self.repo = base_url.rstrip("/"), token, repo

    async def _post(self, path: str, body: dict) -> dict | None:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(f"{self.base_url}{path}", json=body,
                                  headers={"X-Voice-Token": self.token},
                                  timeout=aiohttp.ClientTimeout(total=5)) as r:
                    return await r.json() if r.status == 200 else None
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            # TimeoutError (not a ClientError) is what ClientTimeout raises on a
            # connected-but-hung listener — must be soft too, or j!wake raises.
            log.warning("listener call %s failed: %s", path, exc)
            return None

    async def session_started(self, guild_id: int, channel_id: str) -> None:
        state = await self.repo.get_state(str(guild_id)) or {}
        await self._post("/session", {
            "guild_id": str(guild_id), "channel_id": channel_id,
            "wake_phrase": state.get("wakePhrase") or "hey jacky",
            "action": "join",
        })

    async def session_ended(self, guild_id: int) -> None:
        await self._post("/session", {
            "guild_id": str(guild_id), "channel_id": None,
            "wake_phrase": "", "action": "leave",
        })

    async def validate_phrase(self, phrase: str) -> dict | None:
        return await self._post("/validate", {"phrase": phrase})
