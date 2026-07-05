"""LavalinkVoiceClient: handshake gating and voice-route-flap detection."""

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from jacky.audio.provider import SingleNodeProvider
from jacky.audio.voice import LavalinkVoiceClient
from tests.conftest import FakeNode


@dataclass
class FakeVCGuild:
    id: int = 123
    voice_states: list = field(default_factory=list)

    async def change_voice_state(self, *, channel, self_deaf=False, self_mute=False):
        self.voice_states.append(channel)

    def get_channel(self, channel_id):
        return None


class RecordingService:
    def __init__(self):
        self.recoveries: list[tuple[int, str]] = []

    async def recover_playback(self, guild_id, reason):
        self.recoveries.append((guild_id, reason))


def make_voice():
    node = FakeNode()
    service = RecordingService()
    guild = FakeVCGuild()
    channel = SimpleNamespace(id=42, name="General", guild=guild)
    client = SimpleNamespace(node_provider=SingleNodeProvider(node), service=service)
    voice = LavalinkVoiceClient(client, channel)
    return voice, node, service, guild


SERVER_DATA = {"token": "tok", "endpoint": "atlanta1.discord.media:443"}
STATE_DATA = {"session_id": "sess-abc", "channel_id": "42"}


async def test_connect_blocks_until_voice_reaches_lavalink():
    voice, node, _service, guild = make_voice()

    connect_task = asyncio.get_running_loop().create_task(
        voice.connect(timeout=5.0, reconnect=False)
    )
    await asyncio.sleep(0.05)
    assert not connect_task.done()  # gateway events not in yet
    assert guild.voice_states  # but the join was requested

    await voice.on_voice_state_update(dict(STATE_DATA))
    await voice.on_voice_server_update(dict(SERVER_DATA))
    await asyncio.wait_for(connect_task, timeout=1.0)

    gid, payload = node.updates[0]
    assert gid == 123
    assert payload["voice"] == {
        "token": "tok",
        "endpoint": "atlanta1.discord.media:443",
        "sessionId": "sess-abc",
        "channelId": "42",
    }


async def test_connect_times_out_without_handshake():
    voice, _node, _service, _guild = make_voice()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(voice.connect(timeout=0.2, reconnect=False), timeout=2.0)


async def test_endpoint_change_triggers_recovery():
    voice, node, service, _guild = make_voice()
    await voice.on_voice_state_update(dict(STATE_DATA))
    await voice.on_voice_server_update(dict(SERVER_DATA))
    assert service.recoveries == []  # first handshake is not a flap

    await voice.on_voice_server_update(
        {"token": "tok2", "endpoint": "south-korea13042.discord.media:443"}
    )
    await asyncio.sleep(0.05)  # recovery is scheduled as a task
    assert service.recoveries == [(123, "voice endpoint changed")]
    assert node.updates[-1][1]["voice"]["endpoint"] == "south-korea13042.discord.media:443"


async def test_same_endpoint_repush_is_not_a_flap():
    voice, _node, service, _guild = make_voice()
    await voice.on_voice_state_update(dict(STATE_DATA))
    await voice.on_voice_server_update(dict(SERVER_DATA))
    await voice.on_voice_server_update(dict(SERVER_DATA))  # token refresh, same endpoint
    await asyncio.sleep(0.05)
    assert service.recoveries == []
