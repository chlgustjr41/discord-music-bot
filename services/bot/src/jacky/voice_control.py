"""Dispatch interpreted voice actions onto PlayerService.

Ported from the shelved feat/voice-control branch — the layer that always
worked; only the Discord voice-receive acquisition below it ever failed.
There is no verb that ends a session: one misrecognition would end it and
clear the queue with no undo, and a dedicated Stop key exists. Stop-like
speech maps to `pause` instead, for that same reason.
"""

import logging
from dataclasses import dataclass
from typing import Any

from jacky.api.dashboard_link import entry_url, session_url
from jacky.api.voice_actions import Action
from jacky.api.voice_grammar import normalize_playlist_name
from jacky.audio.models import to_track_data

log = logging.getLogger("jacky.voice")

VOLUME_STEP = 10

# Maps an announce VERB to the command name the shared Announcer speaks
# (which is also the j! name). The posting itself — content checks, embeds,
# the one shared cooldown window — lives in jacky/announce.py.
_ANNOUNCE_COMMAND_FOR = {
    "session_info": "session",
    "now_playing": "nowplaying",
    "queue_info": "queue",
    "status_info": "status",
}


@dataclass(frozen=True)
class DispatchResult:
    ok: bool
    detail: str = ""
    # Overrides the spoken argument when logging to command history, so a
    # volume row
    # records the resulting level ("60") rather than an empty arg — which
    # both keeps up/down as distinct rows and makes retrigger work.
    log_arg: str | None = None
    # Set only by actions the SERVER cannot perform — currently just
    # open_dashboard. The route collects these into the response's `client`
    # array and the plugin executes them. None for every server-side action.
    client: dict | None = None


class VoiceIntentDispatcher:
    def __init__(self, service: Any, repo: Any, announcer: Any) -> None:
        # The SAME Announcer instance the announce endpoint uses (core/bot.py
        # constructs one and hands it to both): the 10 s window is shared, so
        # a voice announce cools down the Post key and vice versa.
        self.service, self.repo, self.announcer = service, repo, announcer

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
            # NOT repo.clear_queue: that also nulls currentTrack, which is
            # right for its teardown callers but wrong here. listener.py reads
            # "currentTrack nulled while isPlaying stays true" as a web skip
            # and skips, on_track_end then advances into the now-empty queue,
            # and the music stops — while the key reports "Queue cleared".
            # The spec bounds this action to the queue: the track keeps playing.
            await self.repo.update_state(sid, {"queue": []})
            return DispatchResult(True, "Queue cleared")
        if kind == "loop":
            await self.repo.update_state(sid, {"loopMode": action.mode})
            return DispatchResult(True, f"Loop {action.mode}")
        if kind == "play":
            return await self._play(guild_id, sid, action)
        if kind == "playlist":
            return await self._playlist_action(guild_id, sid, action)
        if kind in ("now_playing", "session_info", "queue_info", "status_info"):
            return await self._announce(guild_id, kind)
        if kind == "open_dashboard":
            return await self._open_dashboard(sid)
        return DispatchResult(False, "Unknown command")

    async def _announce(self, guild_id: int, kind: str) -> DispatchResult:
        """One call into the shared Announcer; render the outcome as a
        DispatchResult. The cooldown flag is deliberately dropped here — the
        voice key renders `detail`, and the Announcer's cooldown sentence IS
        the rendering."""
        outcome = await self.announcer.post(guild_id, _ANNOUNCE_COMMAND_FOR[kind])
        # log_arg only on success, exactly as before the extraction: history
        # args carry what was POSTED (the title, the code), and a failed
        # announce falls back to the route's default empty args.
        return DispatchResult(
            outcome.ok, outcome.detail,
            log_arg=outcome.detail if outcome.ok else None,
        )

    async def _open_dashboard(self, sid: str) -> DispatchResult:
        """No server-side effect at all: the browser lives on the client, so
        this only hands the plugin a URL to open."""
        web = self.service.settings.web_app_url
        code = (await self.repo.get_state(sid) or {}).get("sessionCode")
        url = session_url(web, code) if code else entry_url(web)
        return DispatchResult(
            True, "Opening dashboard",
            client={"type": "open_url", "url": url},
        )

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
            # None-check again, for the same reason: delta 0 is an explicit
            # "leave it alone", and `or VOLUME_STEP` would turn it into +10.
            delta = VOLUME_STEP if action.delta is None else action.delta
            new = await self.service.set_volume(guild_id, current + delta)
        return DispatchResult(True, f"Volume {new}", log_arg=str(new))

    async def _play(self, guild_id: int, sid: str, action: Action) -> DispatchResult:
        try:
            result = await self.service.resolve(action.query)
        except Exception as exc:  # noqa: BLE001 — surfaced on the key
            # Type name only. `action.query` is transcribed speech, and so is
            # the exception MESSAGE: node.py raises
            # NodeError("GET /loadtracks?identifier=... -> HTTP ...") with the
            # query URL-encoded into the path, and URL-encoding is not
            # redaction. A query is always words the user spoke — the grammar
            # and the interpreter both extract it literally from the
            # transcript — so logging the message would put speech on
            # container stdout, which has different retention and readers
            # than the command history. Same INVARIANT as dispatch_all's.
            log.warning("voice search failed: %s", type(exc).__name__)
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
