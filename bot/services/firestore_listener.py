import asyncio
from google.cloud.firestore_v1.watch import DocumentChange


class FirestoreListener:
    """Watches a server's Firestore doc for changes from the web app."""

    def __init__(self, bot, fs, server_id: str):
        self.bot = bot
        self.fs = fs
        self.server_id = server_id
        self._unsubscribe = None
        self._last_state = None

    def start(self):
        doc_ref = self.fs.db.collection("servers").document(self.server_id)
        self._unsubscribe = doc_ref.on_snapshot(self._on_snapshot)

    def stop(self):
        if self._unsubscribe:
            self._unsubscribe.unsubscribe()
            self._unsubscribe = None

    def _on_snapshot(self, doc_snapshot, changes, read_time):
        for doc in doc_snapshot:
            new_state = doc.to_dict()
            if self._last_state is None:
                self._last_state = new_state
                continue

            old = self._last_state
            self._last_state = new_state

            # Detect web-triggered changes and sync to player
            asyncio.run_coroutine_threadsafe(
                self._handle_changes(old, new_state),
                self.bot.loop,
            )

    async def _handle_changes(self, old: dict, new: dict):
        guild = self.bot.get_guild(int(self.server_id))
        if not guild:
            return
        player = guild.voice_client
        if not player:
            return

        # Volume change from web
        if old.get("volume") != new.get("volume"):
            await player.set_volume(new.get("volume", 80))

        # Pause/resume from web
        if old.get("isPaused") != new.get("isPaused"):
            await player.pause(new.get("isPaused", False))

        # Skip (web sets currentTrack to None while isPlaying is True)
        if (old.get("currentTrack") is not None and
                new.get("currentTrack") is None and
                new.get("isPlaying", False)):
            await player.stop()  # triggers on_wavelink_track_end -> play_next

        # Shuffle triggered from web (detected by queue order change)
        # No action needed — queue is read from Firestore on next play_next call

        # Loop mode change — no player action needed, read on track end
