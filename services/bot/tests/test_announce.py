"""The shared Announcer: one posting path for the Post key and for voice.

Direct tests over the class itself — the two consumers' contract tests live
in test_control_api.py and test_voice_control.py. What is pinned HERE are the
invariants the extraction exists to keep in one place: content checks before
the cooldown, the stamp only on a successful post, and ONE shared per-guild
window across all four commands.
"""

import pytest

from jacky.announce import ANNOUNCE_COOLDOWN_S, Announcer


@pytest.fixture
def announcer(service):
    return Announcer(service, service.bot)


# ── all four commands post the right embed ───────────────────────────────


async def test_session_posts_the_session_embed(announcer, service, guild_id, sid):
    await service.repo.set_session_code(sid, "ABC123")
    outcome = await announcer.post(guild_id, "session")
    assert outcome.ok is True and outcome.cooldown is False
    assert "ABC123" in outcome.detail
    [post] = service.fake_notifier.sent
    assert post["guild_id"] == guild_id
    # The j!session embed, not merely "an embed": the code is in its body.
    assert "ABC123" in post["embed"].description


async def test_nowplaying_posts_the_current_track(announcer, service, guild_id, sid):
    await service.repo.update_state(sid, {
        "currentTrack": {"title": "Song A", "artist": "Artist B", "duration": 63},
    })
    outcome = await announcer.post(guild_id, "nowplaying")
    assert outcome.ok is True
    assert "Song A" in outcome.detail
    [post] = service.fake_notifier.sent
    embed = post["embed"]
    assert embed.title == "Now Playing"
    assert "Song A" in embed.description and "Artist B" in embed.description


async def test_queue_posts_the_queue_embed(announcer, service, guild_id, sid):
    await service.repo.update_state(sid, {
        "currentTrack": {"title": "Current"},
        "queue": [{"title": "First", "duration": 61}, {"title": "Second"}],
    })
    outcome = await announcer.post(guild_id, "queue")
    assert outcome.ok is True
    [post] = service.fake_notifier.sent
    embed = post["embed"]
    assert embed.title == "Queue"
    assert "First" in embed.description and "Second" in embed.description
    assert any("Current" in (f.value or "") for f in embed.fields)


async def test_status_posts_the_status_embed(announcer, service, guild_id, sid):
    await service.repo.update_state(sid, {
        "currentTrack": {"title": "Song A", "duration": 100},
        "queue": [{"title": "b"}],
    })
    service.positions[guild_id] = {"connected": True, "position": 5000}
    outcome = await announcer.post(guild_id, "status")
    assert outcome.ok is True
    [post] = service.fake_notifier.sent
    embed = post["embed"]
    assert embed.title == "🩺 Jacky Music — System Status"
    fields = {f.name: f.value for f in embed.fields}
    assert "Song A" in fields["This server"]
    # No Status cog on the FakeBot: the uptime line is OMITTED, and the post
    # still happens — a missing cosmetic line must not break a health report.
    assert "up " not in fields["Bot"]


# ── empty content fails on the caller and posts nothing ─────────────────


async def test_session_without_a_code_posts_nothing(announcer, service, guild_id):
    outcome = await announcer.post(guild_id, "session")
    assert outcome.ok is False and outcome.cooldown is False
    assert outcome.detail == "No session code"
    assert service.fake_notifier.sent == []


async def test_nowplaying_with_nothing_playing_posts_nothing(
    announcer, service, guild_id
):
    outcome = await announcer.post(guild_id, "nowplaying")
    assert outcome.ok is False
    assert outcome.detail == "Nothing is playing"
    assert service.fake_notifier.sent == []


async def test_an_empty_queue_posts_nothing(announcer, service, guild_id, sid):
    assert await service.repo.get_queue(sid) == []
    outcome = await announcer.post(guild_id, "queue")
    assert outcome.ok is False
    assert outcome.detail == "Queue is empty"
    assert service.fake_notifier.sent == []


async def test_an_unknown_command_is_inert(announcer, service, guild_id):
    """Callers allowlist before calling, but the Announcer is the last line:
    an unknown command must be inert, never an exception or a post."""
    outcome = await announcer.post(guild_id, "play")
    assert outcome.ok is False
    assert service.fake_notifier.sent == []


# ── ordering: content checks come BEFORE the cooldown ───────────────────


async def test_empty_content_reports_itself_not_the_cooldown(
    announcer, service, guild_id, sid
):
    """With the window ALREADY burnt by a success, a call that had nothing to
    post must still name the real reason — never be blamed on a window it did
    not even try to use."""
    await service.repo.set_session_code(sid, "ABC123")
    assert (await announcer.post(guild_id, "session")).ok is True
    outcome = await announcer.post(guild_id, "queue")
    assert outcome.ok is False and outcome.cooldown is False
    assert outcome.detail == "Queue is empty"


# ── the stamp lands only on a successful post ────────────────────────────


async def test_a_failed_send_does_not_start_the_cooldown(
    announcer, service, guild_id, sid
):
    """One Discord hiccup must not silently block the next 10 seconds of
    legitimate posts: the retry succeeds IMMEDIATELY."""
    await service.repo.update_state(sid, {"currentTrack": {"title": "Song"}})
    service.fake_notifier.fail = True
    outcome = await announcer.post(guild_id, "nowplaying")
    assert outcome.ok is False and outcome.cooldown is False
    assert outcome.detail == "Could not post to Discord"

    service.fake_notifier.fail = False
    assert (await announcer.post(guild_id, "nowplaying")).ok is True


# ── ONE shared window across the commands ────────────────────────────────


async def test_the_window_is_shared_across_commands(
    announcer, service, guild_id, sid
):
    """The genuinely new behaviour: a `session` post blocks an immediate
    `nowplaying` post. Two features posting into one channel share one spam
    bound (spec: 2026-08-14-voice-announce-unification-design)."""
    await service.repo.update_state(
        sid, {"sessionCode": "ABC123", "currentTrack": {"title": "Song"}}
    )
    assert (await announcer.post(guild_id, "session")).ok is True
    outcome = await announcer.post(guild_id, "nowplaying")
    assert outcome.ok is False
    assert outcome.cooldown is True
    assert outcome.detail == "Just posted — try again shortly"
    assert len(service.fake_notifier.sent) == 1


async def test_the_window_is_per_guild(announcer, service, guild_id, sid):
    from tests.conftest import FakeGuild, FakeVoice

    other = FakeGuild(id=777, voice_client=FakeVoice(), name="Other")
    service.bot.guilds.append(other)
    await service.repo.init_state("777")
    await service.repo.set_session_code(sid, "ABC123")
    await service.repo.set_session_code("777", "ZZZ999")

    assert (await announcer.post(guild_id, "session")).ok is True
    assert (await announcer.post(777, "session")).ok is True


async def test_the_window_expires(announcer, service, guild_id, sid):
    await service.repo.set_session_code(sid, "ABC123")
    assert (await announcer.post(guild_id, "session")).ok is True
    later = announcer.now() + ANNOUNCE_COOLDOWN_S + 1
    announcer.now = lambda: later
    assert (await announcer.post(guild_id, "session")).ok is True
