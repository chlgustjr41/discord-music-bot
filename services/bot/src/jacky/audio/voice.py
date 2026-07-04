"""discord.py VoiceProtocol that routes voice credentials to Lavalink.

The bot never opens a UDP voice socket — Lavalink does. This class only
captures VOICE_STATE_UPDATE / VOICE_SERVER_UPDATE from the Discord gateway
and forwards them to the node. The last complete voice payload is cached so
that a restarted (non-resumed) Lavalink session can be re-primed without
touching Discord at all — the fix for the legacy bot's silent-after-restart
failure class (spec §1 Class B).
"""

import logging

import discord

log = logging.getLogger("jacky.voice")


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
