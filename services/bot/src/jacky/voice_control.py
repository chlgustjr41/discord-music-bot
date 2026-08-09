"""Dispatch parsed voice intents onto PlayerService.

Ported from the shelved feat/voice-control branch — the layer that always
worked; only the Discord voice-receive acquisition below it ever failed.
`stop` is deliberately absent: one misrecognition would end the session and
clear the queue with no undo, and a dedicated Stop key exists.
"""

import logging
from dataclasses import dataclass
from typing import Any

from jacky.api.voice_actions import Action
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

    async def dispatch_all(
        self, guild_id: int, actions: list[Action]
    ) -> list[DispatchResult]:
        """Run actions in order. One failure never blocks the rest — the user
        asked for several things and should get the ones that work."""
        results: list[DispatchResult] = []
        for action in actions:
            try:
                results.append(await self._dispatch_action(guild_id, action))
            except Exception:  # noqa: BLE001 — contained per action
                # INVARIANT: log the verb only. `action` carries the user's
                # transcribed speech in `query`/`name`, and this reaches
                # container stdout, which has different retention and readers
                # than the command history transcripts are persisted to.
                log.exception("voice action failed: %s", action.action)
                results.append(DispatchResult(False, f"{action.action} failed"))
        return results

    async def _dispatch_action(self, guild_id: int, action: Action) -> DispatchResult:
        sid = str(guild_id)
        kind = action.action
        if kind == "pause":
            await self.service.pause(guild_id, True)
            return DispatchResult(True, "Paused")
        if kind == "resume":
            await self.service.pause(guild_id, False)
            return DispatchResult(True, "Resumed")
        if kind == "skip":
            return await self._skip(guild_id, sid, action.count)
        if kind == "volume":
            return await self._volume(guild_id, sid, action)
        if kind == "shuffle":
            count = await self.repo.shuffle_queue(sid)
            return DispatchResult(True, f"Shuffled {count}")
        if kind == "clear_queue":
            await self.repo.clear_queue(sid)
            return DispatchResult(True, "Queue cleared")
        if kind == "loop":
            await self.repo.update_state(sid, {"loopMode": action.mode})
            return DispatchResult(True, f"Loop {action.mode}")
        if kind == "play":
            return await self._play(guild_id, sid, action)
        if kind == "playlist":
            return await self._playlist_action(guild_id, sid, action)
        return DispatchResult(False, "Unknown command")

    async def _skip(self, guild_id: int, sid: str, count: int) -> DispatchResult:
        # Pop count-1 first, then skip once. Calling skip() repeatedly would
        # race the TrackEnd-driven auto-advance and drop an unpredictable
        # number of tracks.
        if count > 1:
            queue = await self.repo.get_queue(sid)
            await self.repo.update_state(sid, {"queue": queue[count - 1:]})
        await self.service.skip(guild_id)
        return DispatchResult(True, "Skipped" if count == 1 else f"Skipped {count}")

    async def _volume(self, guild_id: int, sid: str, action: Action) -> DispatchResult:
        if action.level is not None:
            new = await self.service.set_volume(guild_id, action.level)
        else:
            state = await self.repo.get_state(sid) or {}
            current = state.get("volume")
            # None-check, not `or` — volume 0 is a real muted level, and
            # falsy-defaulting would report 80 for a muted session.
            current = 80 if current is None else int(current)
            new = await self.service.set_volume(
                guild_id, current + (action.delta or VOLUME_STEP)
            )
        return DispatchResult(True, f"Volume {new}", log_arg=str(new))

    async def _play(self, guild_id: int, sid: str, action: Action) -> DispatchResult:
        try:
            result = await self.service.resolve(action.query)
        except Exception as exc:  # noqa: BLE001 — surfaced on the key
            # Deliberately NOT logging `action.query`: it is transcribed speech.
            log.warning("voice search failed: %s", exc)
            return DispatchResult(False, "Search failed")
        if not result.tracks:
            return DispatchResult(False, "No results")
        td = to_track_data(result.first, "voice command")

        if action.placement == "now":
            # Replaces playback outright. Lavalink reports the TrackEnd reason
            # as "replaced", which on_track_end ignores, so there is no
            # competing auto-advance and the old track is simply dropped.
            ok = await self.service.start_current_track(guild_id, result.first, td)
            return DispatchResult(bool(ok), td["title"] if ok else "Playback failed",
                                  log_arg=action.query)

        existing = await self.repo.get_queue(sid)
        # Decide before the write: the queue write wakes the Firestore
        # listener, which auto-starts playback when it sees the queue grow
        # while idle. No await may sit between the write and the start call.
        playing = bool((await self.repo.get_state(sid) or {}).get("currentTrack"))
        queue = [td, *existing] if action.placement == "next" else [*existing, td]
        await self.repo.update_state(sid, {"queue": queue})
        if not playing:
            await self.service.play_next(guild_id)
        return DispatchResult(True, td["title"], log_arg=action.query)

    async def _playlist_action(
        self, guild_id: int, sid: str, action: Action
    ) -> DispatchResult:
        wanted = normalize_playlist_name(action.name)
        saved = await self.repo.list_playlists(sid)
        match = next(
            (p for p in saved if normalize_playlist_name(p.get("name", "")) == wanted),
            None,
        )
        tracks = (match or {}).get("tracks") or []
        if not tracks:
            return DispatchResult(False, f"No playlist called {action.name}")
        queued = [{**t, "requestedBy": "voice command"} for t in tracks]
        existing = await self.repo.get_queue(sid)
        playing = bool((await self.repo.get_state(sid) or {}).get("currentTrack"))
        front = action.placement in ("now", "next")
        await self.repo.update_state(
            sid, {"queue": [*queued, *existing] if front else [*existing, *queued]}
        )
        if action.placement == "now" and playing:
            await self.service.skip(guild_id)
        elif not playing:
            await self.service.play_next(guild_id)
        name = match.get("name", action.name)
        return DispatchResult(True, f"{name} ({len(queued)})", log_arg=name)

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
