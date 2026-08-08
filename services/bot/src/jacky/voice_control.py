"""Dispatch parsed voice intents onto PlayerService.

Ported from the shelved feat/voice-control branch — the layer that always
worked; only the Discord voice-receive acquisition below it ever failed.
`stop` is deliberately absent: one misrecognition would end the session and
clear the queue with no undo, and a dedicated Stop key exists.
"""

import logging
from dataclasses import dataclass
from typing import Any

from jacky.api.voice_intent import Intent, normalize_playlist_name
from jacky.audio.models import to_track_data

log = logging.getLogger("jacky.voice")

VOLUME_STEP = 10


@dataclass(frozen=True)
class DispatchResult:
    ok: bool
    detail: str = ""
    # Overrides intent.arg when logging to command history, so a volume row
    # records the resulting level ("60") rather than an empty arg — which
    # both keeps up/down as distinct rows and makes retrigger work.
    log_arg: str | None = None


class VoiceIntentDispatcher:
    def __init__(self, service: Any, repo: Any) -> None:
        self.service, self.repo = service, repo

    async def dispatch(self, guild_id: int, intent: Intent) -> DispatchResult:
        sid = str(guild_id)
        kind = intent.kind
        if kind == "skip":
            await self.service.skip(guild_id)
            return DispatchResult(True, "Skipped")
        if kind == "pause":
            await self.service.pause(guild_id, True)
            return DispatchResult(True, "Paused")
        if kind == "resume":
            await self.service.pause(guild_id, False)
            return DispatchResult(True, "Resumed")
        if kind in ("volume_up", "volume_down"):
            state = await self.repo.get_state(sid) or {}
            current = state.get("volume")
            current = 80 if current is None else int(current)
            step = VOLUME_STEP if kind == "volume_up" else -VOLUME_STEP
            new = await self.service.set_volume(guild_id, current + step)
            return DispatchResult(True, f"Volume {new}", log_arg=str(new))
        if kind in ("playlist_play", "playlist_add") and intent.arg:
            return await self._playlist(guild_id, sid, intent)
        if kind == "search" and intent.arg:
            return await self._search(guild_id, sid, intent.arg)
        return DispatchResult(False, "Unknown command")

    async def _search(self, guild_id: int, sid: str, query: str) -> DispatchResult:
        try:
            result = await self.service.resolve(query)
        except Exception as exc:  # noqa: BLE001 — surfaced on the key
            # Deliberately NOT logging `query`: it is the user's transcribed
            # speech. Transcripts are persisted only to the session's command
            # history (an explicit product decision with a known audience);
            # container stdout has different retention and readers.
            log.warning("voice search failed: %s", exc)
            return DispatchResult(False, "Search failed")
        if not result.tracks:
            return DispatchResult(False, f"No results for {query}")
        td = to_track_data(result.first, "voice command")
        state = await self.repo.get_state(sid) or {}
        if state.get("currentTrack"):
            await self.repo.add_to_queue(sid, td)
            return DispatchResult(True, td["title"])
        ok = await self.service.start_current_track(guild_id, result.first, td)
        return DispatchResult(bool(ok), td["title"] if ok else "Playback failed")

    async def _playlist(self, guild_id: int, sid: str, intent: Intent) -> DispatchResult:
        wanted = normalize_playlist_name(intent.arg)
        saved = await self.repo.list_playlists(sid)
        match = next(
            (p for p in saved if normalize_playlist_name(p.get("name", "")) == wanted),
            None,
        )
        tracks = (match or {}).get("tracks") or []
        if not tracks:
            return DispatchResult(False, f"No playlist called {intent.arg}")

        queued = [{**t, "requestedBy": "voice command"} for t in tracks]
        existing = await self.repo.get_queue(sid)
        # Decide BEFORE the write: the queue write is what wakes the Firestore
        # listener, and listener.py auto-starts playback when it sees the queue
        # grow while idle. Any await between the write and the start call is a
        # window for it to pop the track we just inserted.
        playing = bool((await self.repo.get_state(sid) or {}).get("currentTrack"))
        if intent.kind == "playlist_play":
            await self.repo.update_state(sid, {"queue": [*queued, *existing]})
            if playing:
                await self.service.skip(guild_id)
            else:
                await self.service.play_next(guild_id)
        else:
            await self.repo.update_state(sid, {"queue": [*existing, *queued]})
            # Appending must never interrupt the current track.
            if not playing:
                await self.service.play_next(guild_id)
        name = match.get("name", intent.arg)
        return DispatchResult(True, f"{name} ({len(queued)})")
