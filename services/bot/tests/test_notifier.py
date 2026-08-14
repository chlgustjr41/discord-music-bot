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
        # Where the bot is standing: guild.voice_client.channel, the shape
        # discord.py exposes. None models "not connected to voice".
        self.voice_channel: FakeTextChannel | None = FakeTextChannel()
        self.guild = type("FakeGuild", (), {})()
        self.guild.voice_client = type("FakeVoice", (), {})()
        self.guild.voice_client.channel = self.voice_channel

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

    def get_guild(self, guild_id):
        return self.guild


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


async def test_send_reports_false_when_there_is_no_destination_at_all(notifier, fake_bot):
    """An announce action must fail on the key rather than claim success —
    but only when there is genuinely nowhere to post: no stored text channel
    AND the bot is not in voice. A missing text channel alone now falls back
    to the bot's voice-channel chat (see the fallback tests below)."""
    fake_bot.state = {}
    fake_bot.guild.voice_client = None
    assert await notifier.send(1, text="hi") is False


async def test_send_reports_false_when_nothing_resolves(notifier, fake_bot):
    fake_bot.channel = None
    fake_bot.guild.voice_client = None
    assert await notifier.send(1, text="hi") is False


async def test_send_reports_false_when_discord_raises(notifier, fake_bot):
    """Best-effort stays best-effort: it must not raise, only report."""

    async def boom(**_kw):
        raise RuntimeError("discord down")

    fake_bot.channel.send = boom
    assert await notifier.send(1, text="hi") is False


async def test_send_still_returns_true_for_the_existing_track_path(notifier):
    assert await notifier.send(1, track={"title": "Song"}) is True


# ── destination fallback: post where the bot IS ─────────────────────────


async def test_send_falls_back_to_the_bots_voice_channel_chat(notifier, fake_bot):
    """A session born from the deck or the web has no invoking text channel
    (begin_session keeps the prior one, which is None on a server never
    j!-started). The bot is still standing in a voice channel, and a voice
    channel carries its own text chat — so the announcement goes where the
    session actually lives instead of failing."""
    fake_bot.state = {}                       # no textChannelId anywhere
    assert await notifier.send(1, text="hi") is True
    assert len(fake_bot.voice_channel.sent_embeds) == 1
    assert fake_bot.channel.sent_embeds == []


async def test_an_explicit_text_channel_still_wins_over_the_voice_chat(
    notifier, fake_bot
):
    """j!-started sessions keep announcing where the command was typed."""
    assert await notifier.send(1, text="hi") is True
    assert len(fake_bot.channel.sent_embeds) == 1
    assert fake_bot.voice_channel.sent_embeds == []


async def test_a_stale_text_channel_id_falls_back_rather_than_failing(
    notifier, fake_bot
):
    """textChannelId can outlive the channel it names (deleted channel,
    restarted cache). An unresolvable id degrades to the bot's location."""
    fake_bot.channel = None                   # get_channel resolves nothing
    assert await notifier.send(1, text="hi") is True
    assert len(fake_bot.voice_channel.sent_embeds) == 1


async def test_no_text_channel_and_no_voice_is_still_an_honest_failure(
    notifier, fake_bot
):
    fake_bot.state = {}
    fake_bot.guild.voice_client = None
    assert await notifier.send(1, text="hi") is False
