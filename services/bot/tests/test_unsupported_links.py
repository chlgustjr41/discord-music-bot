"""The URL allowlist, pinned at every path that can reach Lavalink.

Lavalink fetches what it is handed, from inside the home network, so a link
outside the allowlist (audio/models.py) must not reach it in ANY form — not
as a URL, and not wrapped as a search. tests/test_models.py pins the rule;
these pin that every caller goes through it: one test per entry point, each
asserting on FakeNode.loads, which records every identifier that got as far
as the node.

The route-level twins (POST /control/voice, POST /control/playlist) live in
test_control_api.py, next to the fixtures they need.
"""

from types import SimpleNamespace

import pytest

from jacky.api.voice_actions import Action
from jacky.audio.models import UNSUPPORTED_LINK_MESSAGE, LoadResult, is_url, supported_url
from jacky.state.listener import ServerDocListener
from tests.conftest import FakeVoice

BAD = "http://192.168.1.1/admin"

BAD_LINKS = [
    BAD,
    "http://169.254.169.254/latest",
    "http://localhost:2333/v4/info",
    "https://youtube.com@evil.test/x",
    "https://youtube.com.evil.test/x",
    "file:///etc/passwd",
]


def assert_only_allowed_reached(node, *forbidden):
    """Nothing URL-shaped reached the node unless it is an allowlisted link,
    and no forbidden string reached it inside anything else (a search)."""
    for identifier in node.loads:
        assert not is_url(identifier) or supported_url(identifier) == identifier, identifier
        for needle in forbidden:
            assert needle not in identifier, identifier


@pytest.fixture
def listener(service, sid):
    return ServerDocListener(service.bot, service.repo, service, sid)


# ── PlayerService.resolve: the choke point everything else calls ─────────

@pytest.mark.parametrize("link", BAD_LINKS)
async def test_resolve_refuses_without_asking_the_node(service, link):
    result = await service.resolve(link)
    assert result.unsupported and result.tracks == []
    assert service.node.loads == []


async def test_resolve_still_loads_allowed_links_and_searches(service):
    await service.resolve("https://www.youtube.com/watch?v=abc123")
    await service.resolve("https://soundcloud.com/a/b")
    await service.resolve("some song")
    assert service.node.loads == [
        "https://www.youtube.com/watch?v=abc123",
        "https://soundcloud.com/a/b",
        "ytsearch:some song",
    ]


# ── state/listener.py: what the web dashboard can write ──────────────────

async def test_dashboard_search_for_an_unsupported_link_finds_nothing(
    listener, service, sid
):
    await service.repo.update_state(sid, {"searchQuery": BAD})
    await listener._handle_changes({}, {"searchQuery": BAD})

    assert service.node.loads == []
    state = await service.repo.get_state(sid)
    # The dashboard waits on searchQuery clearing — it must not hang.
    assert state["searchResults"] == [] and state["searchQuery"] is None


async def test_dashboard_search_with_a_non_string_query_does_not_raise(
    listener, service, sid
):
    await listener._handle_changes({}, {"searchQuery": {"$ne": None}})
    assert service.node.loads == []
    assert (await service.repo.get_state(sid))["searchResults"] == []


async def test_dashboard_queue_append_of_an_unsupported_link_is_never_loaded(
    listener, service, guild_id, sid
):
    item = {"title": BAD, "url": BAD, "requestedBy": "Web User"}
    await service.repo.update_state(sid, {"queue": [dict(item)]})

    # Idle with a voice client, so the listener goes on to auto-start: the
    # item is popped and must fall out through the failure path, unloaded.
    await listener._handle_changes({"queue": []}, {"queue": [dict(item)]})

    assert service.node.loads == []
    assert service.node.updates == []          # nothing was played
    state = await service.repo.get_state(sid)
    assert state["currentTrack"] is None and state["isPlaying"] is False


async def test_dashboard_queue_append_keeps_resolving_allowed_links(
    listener, service, sid
):
    good = "https://youtu.be/abc123"
    service.playing[int(sid)] = True  # busy: resolve the item, don't start it
    await listener._handle_changes(
        {"queue": []},
        {"queue": [{"title": good, "url": good}], "currentTrack": {"title": "Now"}},
    )
    assert service.node.loads == [good]
    assert (await service.repo.get_state(sid))["queue"][0]["title"] == "Song"


async def test_dashboard_retrigger_play_of_an_unsupported_link_does_nothing(
    listener, service, sid
):
    await listener._handle_changes(
        {}, {"retriggerCommand": {"command": "play", "args": BAD}}
    )
    assert service.node.loads == []
    assert service.node.updates == []
    state = await service.repo.get_state(sid)
    assert state["queue"] == [] and state["currentTrack"] is None
    assert state["retriggerCommand"] is None   # the request is still consumed


# ── audio/player.py: the play-time re-resolve of STORED track data ───────
# Queue items, the current track and saved playlists all live in documents a
# web client can write, so a stored `url` is as untrusted as a typed one.

async def test_reresolve_falls_back_to_a_plain_title(service, guild_id):
    track = await service._resolve_track_data(
        guild_id, {"title": "Some Song", "artist": "Someone", "url": BAD}
    )
    assert track is not None
    assert service.node.loads == ["ytsearch:Some Song"]
    assert_only_allowed_reached(service.node, BAD, "192.168")


async def test_reresolve_falls_back_to_title_then_title_and_artist(service, guild_id):
    service.node.load_results["ytsearch:Some Song"] = LoadResult(kind="empty")
    await service._resolve_track_data(
        guild_id, {"title": "Some Song", "artist": "Someone", "url": BAD}
    )
    assert service.node.loads == ["ytsearch:Some Song", "ytsearch:Some Song Someone"]


@pytest.mark.parametrize("td", [
    {"title": BAD, "artist": "", "url": BAD},
    {"title": BAD, "artist": "Someone", "url": BAD},
    # The title must not stand in for a refused link even when the title is
    # itself an ALLOWED link: a refused url means the item is not trusted to
    # say where it points.
    {"title": "https://youtu.be/abc123", "artist": "", "url": BAD},
    # No url at all: the title is all there is, and it is not loadable.
    {"title": BAD, "artist": "Someone"},
    {"title": BAD, "url": ""},
    # Malformed shapes a web client can write.
    {"title": None, "url": None},
    {"title": 5, "url": {"x": 1}},
    {},
])
async def test_reresolve_of_an_unloadable_track_is_unresolvable_without_retries(
    service, guild_id, td
):
    import asyncio

    # Retrying cannot make a refused link loadable, so there must be no
    # backoff either: with the production-sized delay restored, a retry loop
    # would sleep straight through this timeout.
    service.search_retry_delay = 30
    track = await asyncio.wait_for(
        service._resolve_track_data(guild_id, td), timeout=2
    )
    assert track is None
    assert service.node.loads == []


async def test_reresolve_of_an_allowed_link_is_unchanged(service, guild_id):
    service.node.load_results["https://youtu.be/abc123"] = LoadResult(kind="empty")
    await service._resolve_track_data(
        guild_id,
        {"title": "Some Song", "artist": "Someone", "url": "https://youtu.be/abc123"},
    )
    assert service.node.loads[:2] == [
        "https://youtu.be/abc123", "ytsearch:Some Song Someone",
    ]


async def test_play_next_skips_an_unsupported_track_and_plays_the_next(
    service, guild_id, sid
):
    await service.repo.update_state(sid, {"queue": [
        {"title": BAD, "url": BAD},
        {"title": "Good", "artist": "", "url": "https://youtu.be/abc123"},
    ]})
    await service.play_next(guild_id)

    assert service.node.loads == ["https://youtu.be/abc123"]
    state = await service.repo.get_state(sid)
    assert state["currentTrack"]["url"] == "https://youtu.be/abc123"
    assert state["queue"] == []
    # The existing failure path: the skipped item is reported, not silent.
    assert any(m.get("error") for m in service.fake_notifier.sent)


async def test_loop_track_with_a_tampered_current_track_never_loads_it(
    service, guild_id, sid
):
    await service.repo.set_current_track(sid, {"title": BAD, "url": BAD})
    await service.repo.update_state(sid, {"loopMode": "track"})
    await service.on_track_end(guild_id, "finished")
    assert service.node.loads == []


async def test_resume_with_a_tampered_current_track_never_loads_it(
    service, guild_id, sid
):
    assert await service.resume_track(guild_id, {"title": BAD, "url": BAD}) is False
    assert service.node.loads == []
    assert service.node.updates == []


# ── voice_control.py: the push-to-talk key's play action ─────────────────

@pytest.fixture
def dispatcher(service):
    from jacky.announce import Announcer
    from jacky.voice_control import VoiceIntentDispatcher

    return VoiceIntentDispatcher(
        service, service.repo, Announcer(service, service.bot)
    )


@pytest.mark.parametrize("placement", ["now", "next", "end"])
async def test_voice_play_of_an_unsupported_link_says_so_on_the_key(
    dispatcher, service, guild_id, sid, placement
):
    await service.repo.update_state(sid, {"currentTrack": {"title": "Now"}})
    [result] = await dispatcher.dispatch_all(
        guild_id, [Action("play", query=BAD, placement=placement)]
    )
    assert result.ok is False
    assert result.detail == UNSUPPORTED_LINK_MESSAGE
    assert result.detail != "No results"
    assert service.node.loads == []
    state = await service.repo.get_state(sid)
    assert state["queue"] == [] and state["currentTrack"] == {"title": "Now"}


async def test_voice_playlist_with_a_tampered_track_never_loads_it(
    dispatcher, service, guild_id, sid
):
    await service.repo.save_playlist(
        sid, "Chill", [{"title": BAD, "url": BAD}, {"title": "P2", "url": BAD}], "x"
    )
    [result] = await dispatcher.dispatch_all(
        guild_id, [Action("playlist", name="chill", placement="end")]
    )
    assert result.ok   # the playlist was queued; its bad entry fails at play time
    assert service.node.loads == ["ytsearch:P2"]
    assert_only_allowed_reached(service.node, BAD)


# ── commands/playback.py and commands/library.py: j! commands ────────────

class FakeCtx:
    def __init__(self, guild, voice_client):
        self.guild = guild
        self.voice_client = voice_client
        self.author = SimpleNamespace(
            id=42, display_name="Tester", voice=SimpleNamespace(channel=None)
        )
        self.command = SimpleNamespace(name="play")
        self.channel = SimpleNamespace(id=99)
        self.sent: list = []

    async def send(self, *, embed=None):
        self.sent.append(embed)


@pytest.fixture
def cog_bot(service):
    bot = service.bot
    bot.service, bot.repo, bot.settings = service, service.repo, service.settings
    return bot


@pytest.fixture
def ctx(service, guild_id):
    guild = service.bot.get_guild(guild_id)
    return FakeCtx(guild, guild.voice_client or FakeVoice())


@pytest.mark.parametrize("link", BAD_LINKS)
async def test_j_play_of_an_unsupported_link_replies_and_loads_nothing(
    cog_bot, service, sid, ctx, link
):
    from jacky.commands.playback import Playback

    cog = Playback(cog_bot)
    await cog.play.callback(cog, ctx, query=link)

    assert service.node.loads == []
    assert service.node.updates == []
    [embed] = ctx.sent
    assert embed.description == UNSUPPORTED_LINK_MESSAGE
    assert "No results" not in embed.description
    assert (await service.repo.get_state(sid))["queue"] == []


async def test_j_play_keeps_its_own_spotify_reply(cog_bot, service, ctx):
    from jacky.commands.playback import Playback

    cog = Playback(cog_bot)
    await cog.play.callback(cog, ctx, query="https://open.spotify.com/track/abc")
    assert service.node.loads == []
    [embed] = ctx.sent
    assert "Spotify" in embed.description


async def test_j_play_still_plays_allowed_links_and_searches(cog_bot, service, sid, ctx):
    from jacky.commands.playback import Playback

    cog = Playback(cog_bot)
    await cog.play.callback(cog, ctx, query="https://youtu.be/abc123")
    await cog.play.callback(cog, ctx, query="some song")
    assert service.node.loads == ["https://youtu.be/abc123", "ytsearch:some song"]
    assert (await service.repo.get_state(sid))["currentTrack"]["title"] == "Song"


async def test_j_playlist_load_with_a_tampered_track_never_loads_it(
    cog_bot, service, guild_id, sid, ctx
):
    from jacky.commands.library import Library

    await service.repo.save_playlist(
        sid, "Chill", [{"title": BAD, "url": BAD}, {"title": "P2", "url": BAD}], "x"
    )
    cog = Library(cog_bot)
    await cog.load.callback(cog, ctx, name="Chill")
    assert len((await service.repo.get_state(sid))["queue"]) == 2

    await service.play_next(guild_id)
    # Entry 1 has nothing loadable and is skipped; entry 2 falls back to its
    # plain title. The stored link reaches the node in neither case.
    assert service.node.loads == ["ytsearch:P2"]
    assert_only_allowed_reached(service.node, BAD)
