"""Firestore repositories. Firestore is the single source of truth (ADR-0003).

Schema is the pre-existing contract shared with the web dashboard
(frontend/ + functions/) — collections `servers`, `serverOwners`,
`sessionCodes` and the per-server subcollections. Field names must not
change without a coordinated frontend release.

The google-cloud-firestore client is synchronous; every public method here
is async and hops to a worker thread so Firestore latency never blocks the
gateway heartbeat.
"""

import asyncio
import random
import string
from typing import Any

from firebase_admin import firestore

SESSION_CODE_LENGTH = 6


def generate_session_code(length: int = SESSION_CODE_LENGTH) -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=length))


class ServerRepository:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def _run(self, fn, *args):
        return await asyncio.to_thread(fn, *args)

    # ── activation ──────────────────────────────────────────────────────

    def _is_activated(self, server_id: str) -> bool:
        doc = self.db.collection("serverOwners").document(server_id).get()
        return doc.exists and doc.to_dict().get("isActive", False)

    async def is_activated(self, server_id: str) -> bool:
        return await self._run(self._is_activated, server_id)

    # ── server state ────────────────────────────────────────────────────

    def _get_state(self, server_id: str) -> dict | None:
        doc = self.db.collection("servers").document(server_id).get()
        return doc.to_dict() if doc.exists else None

    async def get_state(self, server_id: str) -> dict | None:
        return await self._run(self._get_state, server_id)

    def _update_state(self, server_id: str, data: dict) -> None:
        self.db.collection("servers").document(server_id).set(data, merge=True)

    async def update_state(self, server_id: str, data: dict) -> None:
        await self._run(self._update_state, server_id, data)

    def _init_state(self, server_id: str) -> None:
        ref = self.db.collection("servers").document(server_id)
        if not ref.get().exists:
            ref.set({
                "sessionCode": None,
                "currentTrack": None,
                "queue": [],
                "isPlaying": False,
                "isPaused": False,
                "loopMode": "off",
                "volume": 80,
                "voiceChannelId": None,
                "textChannelId": None,
                "idleTimeoutMinutes": 5,
            })

    async def init_state(self, server_id: str) -> None:
        await self._run(self._init_state, server_id)

    def _active_server_ids(self) -> list[str]:
        docs = self.db.collection("servers").stream()
        return [d.id for d in docs if (d.to_dict() or {}).get("voiceChannelId")]

    async def active_server_ids(self) -> list[str]:
        """Servers whose docs claim a live voice session — startup convergence set."""
        return await self._run(self._active_server_ids)

    # ── queue ───────────────────────────────────────────────────────────

    async def get_queue(self, server_id: str) -> list:
        state = await self.get_state(server_id)
        return state.get("queue", []) if state else []

    def _add_to_queue(self, server_id: str, track: dict) -> None:
        self.db.collection("servers").document(server_id).update(
            {"queue": firestore.ArrayUnion([track])}
        )

    async def add_to_queue(self, server_id: str, track: dict) -> None:
        await self._run(self._add_to_queue, server_id, track)

    async def remove_from_queue(self, server_id: str, index: int) -> dict | None:
        queue = await self.get_queue(server_id)
        if not (0 <= index < len(queue)):
            return None
        removed = queue.pop(index)
        await self.update_state(server_id, {"queue": queue})
        return removed

    async def reorder_queue(self, server_id: str, from_idx: int, to_idx: int) -> bool:
        queue = await self.get_queue(server_id)
        if not (0 <= from_idx < len(queue) and 0 <= to_idx < len(queue)):
            return False
        track = queue.pop(from_idx)
        queue.insert(to_idx, track)
        await self.update_state(server_id, {"queue": queue})
        return True

    async def shuffle_queue(self, server_id: str) -> int:
        queue = await self.get_queue(server_id)
        random.shuffle(queue)
        await self.update_state(server_id, {"queue": queue})
        return len(queue)

    async def clear_queue(self, server_id: str) -> None:
        await self.update_state(server_id, {"queue": [], "currentTrack": None})

    async def pop_next_track(self, server_id: str) -> dict | None:
        state = await self.get_state(server_id)
        if not state:
            return None
        queue = state.get("queue", [])
        if not queue:
            return None
        track = queue.pop(0)
        await self.update_state(server_id, {"queue": queue})
        return track

    async def set_current_track(self, server_id: str, track: dict | None) -> None:
        await self.update_state(
            server_id,
            {"currentTrack": track, "isPlaying": track is not None, "isPaused": False},
        )

    # ── session codes ───────────────────────────────────────────────────

    def _set_session_code(self, server_id: str, code: str) -> None:
        self._invalidate_session_code(server_id)
        self._update_state(server_id, {"sessionCode": code})
        self.db.collection("sessionCodes").document(code).set({
            "serverId": server_id,
            "createdAt": firestore.SERVER_TIMESTAMP,
        })

    async def set_session_code(self, server_id: str, code: str) -> None:
        await self._run(self._set_session_code, server_id, code)

    def _invalidate_session_code(self, server_id: str) -> None:
        state = self._get_state(server_id)
        if state and state.get("sessionCode"):
            self.db.collection("sessionCodes").document(state["sessionCode"]).delete()
            self._update_state(server_id, {"sessionCode": None})

    async def invalidate_session_code(self, server_id: str) -> None:
        await self._run(self._invalidate_session_code, server_id)

    # ── search results (web dashboard) ──────────────────────────────────

    async def set_search_results(
        self, server_id: str, results: list, playlist_name: str | None = None
    ) -> None:
        await self.update_state(server_id, {
            "searchResults": results,
            "searchQuery": None,  # cleared to signal completion to the web app
            "searchPlaylistName": playlist_name,
        })

    # ── playlists ───────────────────────────────────────────────────────

    def _save_playlist(self, server_id: str, name: str, tracks: list, created_by: str) -> None:
        (self.db.collection("servers").document(server_id)
         .collection("playlists").document(name).set({
             "name": name,
             "tracks": tracks,
             "createdBy": created_by,
             "createdAt": firestore.SERVER_TIMESTAMP,
         }))

    async def save_playlist(
        self, server_id: str, name: str, tracks: list, created_by: str
    ) -> None:
        await self._run(self._save_playlist, server_id, name, tracks, created_by)

    def _load_playlist(self, server_id: str, name: str) -> dict | None:
        doc = (self.db.collection("servers").document(server_id)
               .collection("playlists").document(name).get())
        return doc.to_dict() if doc.exists else None

    async def load_playlist(self, server_id: str, name: str) -> dict | None:
        return await self._run(self._load_playlist, server_id, name)

    def _list_playlists(self, server_id: str) -> list:
        docs = (self.db.collection("servers").document(server_id)
                .collection("playlists").stream())
        return [{"name": d.id, **d.to_dict()} for d in docs]

    async def list_playlists(self, server_id: str) -> list:
        return await self._run(self._list_playlists, server_id)

    def _delete_playlist(self, server_id: str, name: str) -> None:
        (self.db.collection("servers").document(server_id)
         .collection("playlists").document(name).delete())

    async def delete_playlist(self, server_id: str, name: str) -> None:
        await self._run(self._delete_playlist, server_id, name)

    # ── history ─────────────────────────────────────────────────────────

    def _save_history(
        self, server_id: str, session_id: str, tracks: list, started_at: str, ended_at: str
    ) -> None:
        (self.db.collection("servers").document(server_id)
         .collection("history").document(session_id).set({
             "startedAt": started_at,
             "endedAt": ended_at,
             "tracks": tracks,
         }))

    async def save_history(
        self, server_id: str, session_id: str, tracks: list, started_at: str, ended_at: str
    ) -> None:
        await self._run(self._save_history, server_id, session_id, tracks, started_at, ended_at)

    def _get_history(self, server_id: str, limit: int) -> list:
        docs = (self.db.collection("servers").document(server_id)
                .collection("history")
                .order_by("startedAt", direction=firestore.Query.DESCENDING)
                .limit(limit).stream())
        return [{"id": d.id, **d.to_dict()} for d in docs]

    async def get_history(self, server_id: str, limit: int = 10) -> list:
        return await self._run(self._get_history, server_id, limit)

    # ── command / music logs ────────────────────────────────────────────

    def _log_command(
        self, server_id: str, command: str, args: str, user: str, user_id: str
    ) -> None:
        coll = self.db.collection("servers").document(server_id).collection("commandHistory")
        existing = list(
            coll.where("command", "==", command).where("args", "==", args).limit(1).stream()
        )
        if existing:
            existing[0].reference.update({
                "user": user,
                "userId": user_id,
                "timestamp": firestore.SERVER_TIMESTAMP,
                "callCount": firestore.Increment(1),
            })
        else:
            coll.add({
                "command": command,
                "args": args,
                "user": user,
                "userId": user_id,
                "timestamp": firestore.SERVER_TIMESTAMP,
                "callCount": 1,
            })

    async def log_command(
        self, server_id: str, command: str, args: str, user: str, user_id: str
    ) -> None:
        await self._run(self._log_command, server_id, command, args, user, user_id)

    def _log_music(self, server_id: str, track: dict) -> None:
        url = track.get("url", "")
        coll = self.db.collection("servers").document(server_id).collection("musicHistory")
        if url:
            existing = list(coll.where("url", "==", url).limit(1).stream())
            if existing:
                existing[0].reference.update({
                    **track,
                    "addedAt": firestore.SERVER_TIMESTAMP,
                    "playCount": firestore.Increment(1),
                })
                return
        coll.add({**track, "addedAt": firestore.SERVER_TIMESTAMP, "playCount": 1})

    async def log_music(self, server_id: str, track: dict) -> None:
        await self._run(self._log_music, server_id, track)
