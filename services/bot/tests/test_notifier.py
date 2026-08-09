"""ChannelNotifier: prebuilt embeds and honest posted/not-posted reporting."""

import discord
import pytest

from jacky.core.bot import ChannelNotifier


class FakeRepoState:
    """Minimal repo surface ChannelNotifier touches: get_state only."""

    def __init__(self, state: dict | None) -> None:
        self.state = state

    async def get_state(self, sid):
        return self.state


class FakeTextChannel:
    def __init__(self) -> None:
        self.sent_embeds: list = []

    async def send(self, **kwargs):
        self.sent_embeds.append(kwargs.get("embed"))


class FakeNotifierBot:
    def __init__(self) -> None:
        self.repo = FakeRepoState({"textChannelId": "555"})
        self.channel: FakeTextChannel | None = FakeTextChannel()

    @property
    def state(self):
        return self.repo.state

    @state.setter
    def state(self, value):
        self.repo.state = value

    @property
    def sent_embeds(self):
        return self.channel.sent_embeds

    def get_channel(self, channel_id):
        return self.channel


@pytest.fixture
def fake_bot():
    return FakeNotifierBot()


@pytest.fixture
def notifier(fake_bot):
    return ChannelNotifier(fake_bot)


async def test_send_posts_a_prebuilt_embed(notifier, fake_bot):
    embed = discord.Embed(title="hello")
    assert await notifier.send(1, embed=embed) is True
    assert fake_bot.sent_embeds[-1] is embed


async def test_send_prefers_the_prebuilt_embed_over_a_track(notifier, fake_bot):
    """embed wins: an announce action passes a ready-made embed deliberately."""
    embed = discord.Embed(title="hello")
    assert await notifier.send(1, embed=embed, track={"title": "Song"}) is True
    assert fake_bot.sent_embeds[-1] is embed


async def test_send_reports_false_when_there_is_no_text_channel(notifier, fake_bot):
    """An announce action must fail on the key rather than claim success."""
    fake_bot.state = {}
    assert await notifier.send(1, text="hi") is False


async def test_send_reports_false_when_the_channel_is_unresolvable(notifier, fake_bot):
    fake_bot.channel = None
    assert await notifier.send(1, text="hi") is False


async def test_send_reports_false_when_discord_raises(notifier, fake_bot):
    """Best-effort stays best-effort: it must not raise, only report."""

    async def boom(**_kw):
        raise RuntimeError("discord down")

    fake_bot.channel.send = boom
    assert await notifier.send(1, text="hi") is False


async def test_send_still_returns_true_for_the_existing_track_path(notifier):
    assert await notifier.send(1, track={"title": "Song"}) is True
