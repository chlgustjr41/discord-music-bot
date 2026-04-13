from google.cloud.firestore_v1 import DocumentReference
from firebase_admin import firestore
from typing import Optional
import time


class FirestoreClient:
    def __init__(self, db):
        self.db = db

    # --- Server Activation ---

    def is_server_activated(self, server_id: str) -> bool:
        doc = self.db.collection("serverOwners").document(str(server_id)).get()
        return doc.exists and doc.to_dict().get("isActive", False)

    # --- Server State ---

    def get_server_state(self, server_id: str) -> Optional[dict]:
        doc = self.db.collection("servers").document(str(server_id)).get()
        return doc.to_dict() if doc.exists else None

    def update_server_state(self, server_id: str, data: dict):
        self.db.collection("servers").document(str(server_id)).set(data, merge=True)

    def init_server_state(self, server_id: str):
        ref = self.db.collection("servers").document(str(server_id))
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

    # --- Queue Operations ---

    def get_queue(self, server_id: str) -> list:
        state = self.get_server_state(server_id)
        return state.get("queue", []) if state else []

    def add_to_queue(self, server_id: str, track: dict):
        self.db.collection("servers").document(str(server_id)).update({
            "queue": firestore.ArrayUnion([track])
        })

    def remove_from_queue(self, server_id: str, index: int):
        state = self.get_server_state(server_id)
        if state:
            queue = state.get("queue", [])
            if 0 <= index < len(queue):
                queue.pop(index)
                self.update_server_state(server_id, {"queue": queue})

    def clear_queue(self, server_id: str):
        self.update_server_state(server_id, {"queue": [], "currentTrack": None})

    def reorder_queue(self, server_id: str, from_idx: int, to_idx: int):
        state = self.get_server_state(server_id)
        if state:
            queue = state.get("queue", [])
            if 0 <= from_idx < len(queue) and 0 <= to_idx < len(queue):
                track = queue.pop(from_idx)
                queue.insert(to_idx, track)
                self.update_server_state(server_id, {"queue": queue})

    def shuffle_queue(self, server_id: str):
        import random
        state = self.get_server_state(server_id)
        if state:
            queue = state.get("queue", [])
            random.shuffle(queue)
            self.update_server_state(server_id, {"queue": queue})

    # --- Current Track ---

    def set_current_track(self, server_id: str, track: Optional[dict]):
        data = {"currentTrack": track, "isPlaying": track is not None, "isPaused": False}
        self.update_server_state(server_id, data)

    def pop_next_track(self, server_id: str) -> Optional[dict]:
        state = self.get_server_state(server_id)
        if not state:
            return None
        queue = state.get("queue", [])
        if not queue:
            return None
        track = queue.pop(0)
        self.update_server_state(server_id, {"queue": queue})
        return track

    # --- Session Codes ---

    def set_session_code(self, server_id: str, code: str):
        # Invalidate any existing session code first
        self.invalidate_session_code(server_id)
        self.update_server_state(server_id, {"sessionCode": code})
        self.db.collection("sessionCodes").document(code).set({
            "serverId": str(server_id),
            "createdAt": firestore.SERVER_TIMESTAMP,
        })

    def invalidate_session_code(self, server_id: str):
        state = self.get_server_state(server_id)
        if state and state.get("sessionCode"):
            self.db.collection("sessionCodes").document(state["sessionCode"]).delete()
            self.update_server_state(server_id, {"sessionCode": None})

    def resolve_session_code(self, code: str) -> Optional[str]:
        doc = self.db.collection("sessionCodes").document(code).get()
        return doc.to_dict().get("serverId") if doc.exists else None

    # --- Search (web app → bot → results) ---

    def set_search_results(self, server_id: str, results: list):
        self.update_server_state(server_id, {
            "searchResults": results,
            "searchQuery": None,  # Clear query to signal completion
        })

    # --- Playlists ---

    def save_playlist(self, server_id: str, name: str, tracks: list, created_by: str):
        self.db.collection("servers").document(str(server_id)).collection("playlists").document(name).set({
            "name": name,
            "tracks": tracks,
            "createdBy": created_by,
            "createdAt": firestore.SERVER_TIMESTAMP,
        })

    def load_playlist(self, server_id: str, name: str) -> Optional[dict]:
        doc = (self.db.collection("servers").document(str(server_id))
               .collection("playlists").document(name).get())
        return doc.to_dict() if doc.exists else None

    def list_playlists(self, server_id: str) -> list:
        docs = (self.db.collection("servers").document(str(server_id))
                .collection("playlists").stream())
        return [{"name": d.id, **d.to_dict()} for d in docs]

    def delete_playlist(self, server_id: str, name: str):
        (self.db.collection("servers").document(str(server_id))
         .collection("playlists").document(name).delete())

    # --- History (legacy session-based) ---

    def save_history(self, server_id: str, session_id: str, tracks: list,
                     started_at, ended_at):
        self.db.collection("servers").document(str(server_id)).collection("history").document(session_id).set({
            "startedAt": started_at,
            "endedAt": ended_at,
            "tracks": tracks,
        })

    def get_history(self, server_id: str, limit: int = 10) -> list:
        docs = (self.db.collection("servers").document(str(server_id))
                .collection("history")
                .order_by("startedAt", direction=firestore.Query.DESCENDING)
                .limit(limit).stream())
        return [{"id": d.id, **d.to_dict()} for d in docs]

    # --- Command History ---

    def log_command(self, server_id: str, command: str, args: str,
                    user: str, user_id: str):
        """Log a command. If the same command+args already exists, update it."""
        coll = self.db.collection("servers").document(str(server_id)).collection("commandHistory")
        existing = list(
            coll.where("command", "==", command)
                .where("args", "==", args)
                .limit(1).stream()
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

    # --- Music History ---

    def log_music(self, server_id: str, track: dict):
        """Log a track to music history. If the same URL already exists, update it."""
        url = track.get("url", "")
        coll = self.db.collection("servers").document(str(server_id)).collection("musicHistory")
        if url:
            existing = list(coll.where("url", "==", url).limit(1).stream())
            if existing:
                existing[0].reference.update({
                    **track,
                    "addedAt": firestore.SERVER_TIMESTAMP,
                    "playCount": firestore.Increment(1),
                })
                return
        coll.add({
            **track,
            "addedAt": firestore.SERVER_TIMESTAMP,
            "playCount": 1,
        })

    def delete_music_history(self, server_id: str, doc_id: str):
        (self.db.collection("servers").document(str(server_id))
         .collection("musicHistory").document(doc_id).delete())

    # --- Local Node ---

    def get_local_node(self, server_id: str) -> Optional[dict]:
        doc = self.db.collection("serverOwners").document(str(server_id)).get()
        if doc.exists:
            return doc.to_dict().get("localNode")
        return None

    def set_local_node(self, server_id: str, url: str, password: str):
        self.db.collection("serverOwners").document(str(server_id)).set({
            "localNode": {
                "url": url,
                "password": password,
                "connected": True,
                "connectedAt": firestore.SERVER_TIMESTAMP,
            }
        }, merge=True)

    def set_local_node_status(self, server_id: str, connected: bool):
        """Update only the connected flag without touching credentials."""
        self.db.collection("serverOwners").document(str(server_id)).update({
            "localNode.connected": connected,
        })

    def clear_local_node(self, server_id: str):
        self.db.collection("serverOwners").document(str(server_id)).update({
            "localNode": firestore.DELETE_FIELD,
        })
