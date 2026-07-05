"""discord.py VoiceProtocol that routes voice credentials to Lavalink.

The bot never opens a UDP voice socket — Lavalink does. This class only
captures VOICE_STATE_UPDATE / VOICE_SERVER_UPDATE from the Discord gateway
and forwards them to the node. The last complete voice payload is cached so
that a restarted (non-resumed) Lavalink session can be re-primed without
touching Discord at all — the fix for the legacy bot's silent-after-restart
failure class (spec §1 Class B).
"""

import asyncio
import logging

import discord

log = logging.getLogger("jacky.voice")

HANDSHAKE_TIMEOUT = 15.0


class LavalinkVoiceClient(discord.VoiceProtocol):
    def __init__(self, client: discord.Client, channel: discord.abc.Connectable) -> None:
        super().__init__(client, channel)
        self.guild = channel.guild
        # The node comes off the bot instance: channel.connect(cls=...) gives
        # us no way to pass constructor args.
        self._provider = client.node_provider
        self._session_id: str | None = None
        self._server: dict | None = None
        self.last_voice_payload: dict | None = None
        self._destroyed = False
        # Set once the first voice payload has actually reached Lavalink —
        # connect() blocks on it so a play can never race the handshake.
        self._voice_pushed = asyncio.Event()

    @property
    def node(self):
        return self._provider.node_for(self.guild.id)

    async def on_voice_state_update(self, data: dict) -> None:
        self._session_id = data.get("session_id")
        if data.get("channel_id") is None:
            # Kicked/moved out or disconnect completed.
            await self._destroy()
            return
        channel = self.guild.get_channel(int(data["channel_id"]))
        if channel is not None:
            self.channel = channel
        await self._maybe_send()

    async def on_voice_server_update(self, data: dict) -> None:
        self._server = data
        await self._maybe_send()

    async def _maybe_send(self) -> None:
        if not self._session_id or not self._server:
            return
        endpoint = self._server.get("endpoint")
        token = self._server.get("token")
        if not endpoint or not token:
            return
        previous_endpoint = (self.last_voice_payload or {}).get("endpoint")
        payload = {
            "token": token,
            "endpoint": endpoint,
            "sessionId": self._session_id,
        }
        # Lavalink 4.2+ needs channelId for DAVE (E2EE voice) — carried over
        # from the legacy bot's empirically required patch.
        if self.channel:
            payload["channelId"] = str(self.channel.id)
        self.last_voice_payload = payload
        try:
            await self.node.update_player(self.guild.id, {"voice": payload})
        except Exception as exc:
            log.error("voice update rejected for guild %s: %s", self.guild.id, exc)
            return
        self._voice_pushed.set()
        # Discord migrated the voice server mid-session (e.g. region
        # re-evaluation after listeners join): Lavalink's media connection
        # tears down and the playing track stalls silently. Hand recovery to
        # the player service instead of waiting for the guardian's F6.
        if previous_endpoint and previous_endpoint != endpoint:
            log.warning(
                "voice endpoint changed for guild %s: %s -> %s",
                self.guild.id, previous_endpoint, endpoint,
            )
            service = getattr(self.client, "service", None)
            if service:
                asyncio.get_running_loop().create_task(
                    service.recover_playback(self.guild.id, "voice endpoint changed")
                )

    async def resend_voice(self) -> bool:
        """Re-prime a fresh Lavalink session with the cached voice payload."""
        if not self.last_voice_payload:
            return False
        try:
            await self.node.update_player(self.guild.id, {"voice": self.last_voice_payload})
            return True
        except Exception as exc:
            log.error("voice re-send failed for guild %s: %s", self.guild.id, exc)
            return False

    async def connect(
        self, *, timeout: float, reconnect: bool, self_deaf: bool = True, self_mute: bool = False
    ) -> None:
        await self.guild.change_voice_state(
            channel=self.channel, self_deaf=self_deaf, self_mute=self_mute
        )
        # Block until Lavalink actually holds the voice credentials: a play
        # issued before that lands on a voiceless player and sits silent
        # (the "first track never plays" bug). Raising lets discord.py tear
        # the protocol down and the command surface a clear error instead.
        await asyncio.wait_for(
            self._voice_pushed.wait(), timeout=min(timeout or HANDSHAKE_TIMEOUT, HANDSHAKE_TIMEOUT)
        )

    async def disconnect(self, *, force: bool = False) -> None:
        try:
            await self.guild.change_voice_state(channel=None)
        finally:
            await self._destroy()

    async def _destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self.cleanup()
        try:
            await self.node.destroy_player(self.guild.id)
        except Exception as exc:
            log.debug("destroy_player during voice teardown: %s", exc)
