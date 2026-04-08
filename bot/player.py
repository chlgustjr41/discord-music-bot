"""Custom wavelink Player that adds channelId to voice state updates.

Lavalink 4.2.0+ requires channelId in the voice state payload for DAVE
(E2EE voice) support, but wavelink 3.4.1 (archived, no longer maintained)
does not include it. This subclass patches the voice dispatch to add it.
"""

import wavelink


class JackyPlayer(wavelink.Player):

    async def _dispatch_voice_update(self) -> None:
        assert self.guild is not None
        data = self._voice_state["voice"]

        session_id = data.get("session_id")
        token = data.get("token")
        endpoint = data.get("endpoint")

        if not session_id or not token or not endpoint:
            return

        # Lavalink 4.2.0+ requires channelId for DAVE (E2EE voice) support.
        # wavelink 3.4.1 omits it, causing Lavalink to reject the update.
        channel_id = str(self.channel.id) if self.channel else None
        voice_payload = {
            "sessionId": session_id,
            "token": token,
            "endpoint": endpoint,
        }
        if channel_id:
            voice_payload["channelId"] = channel_id

        request = {"voice": voice_payload}

        try:
            await self.node._update_player(self.guild.id, data=request)
        except wavelink.LavalinkException:
            await self.disconnect()
        else:
            self._connected = True
            self._connection_event.set()
