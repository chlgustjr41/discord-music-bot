"""Shared fakes: in-memory repo, recording node, capture notifier, stub bot."""

from dataclasses import dataclass, field

import pytest

from jacky.audio.models import LoadResult


class FakeNotFound(Exception):
    """Mirrors discord.NotFound semantics for FakeGuild.fetch_member."""


class FakeRepo:
    def __init__(self) -> None:
        self.states: dict[str, dict] = {}
        self.music_log: list = []
        self.command_log: list = []
        self.history: list = []
        self.session_codes: dict[str, str] = {}
        self.control_tokens: dict[str, dict] = {}
        # Per-guild activation override; falls back to the `activated` class
        # attribute (all-or-nothing) when a guild id has no entry.
        self.activated_overrides: dict[str, bool] = {}
        # serverId -> playlist name -> doc
        self.playlists: dict[str, dict[str, dict]] = {}

    async def save_playlist(self, sid, name, tracks, created_by):
        self.playlists.setdefault(sid, {})[name] = {
            "name": name, "tracks": list(tracks), "createdBy": created_by,
        }

    async def load_playlist(self, sid, name):
        return self.playlists.get(sid, {}).get(name)

    async def list_playlists(self, sid):
        return list(self.playlists.get(sid, {}).values())

    async def delete_playlist(self, sid, name):
        self.playlists.get(sid, {}).pop(name, None)

    async def init_state(self, sid):
        self.states.setdefault(sid, {
            "sessionCode": None, "currentTrack": None, "queue": [],
            "isPlaying": False, "isPaused": False, "loopMode": "off",
            "volume": 80, "voiceChannelId": None, "textChannelId": None,
        })

    async def get_state(self, sid):
        return self.states.get(sid)

    async def update_state(self, sid, data):
        self.states.setdefault(sid, {}).update(data)

    async def set_current_track(self, sid, track):
        await self.update_state(sid, {
            "currentTrack": track, "isPlaying": track is not None, "isPaused": False,
        })

    async def get_queue(self, sid):
        return list(self.states.get(sid, {}).get("queue", []))

    async def add_to_queue(self, sid, track):
        self.states.setdefault(sid, {}).setdefault("queue", []).append(track)

    async def remove_from_queue(self, sid, index):
        queue = self.states.get(sid, {}).get("queue", [])
        if not (0 <= index < len(queue)):
            return None
        return queue.pop(index)

    async def reorder_queue(self, sid, from_idx, to_idx):
        queue = self.states.get(sid, {}).get("queue", [])
        if not (0 <= from_idx < len(queue) and 0 <= to_idx < len(queue)):
            return False
        queue.insert(to_idx, queue.pop(from_idx))
        return True

    async def shuffle_queue(self, sid):
        return len(self.states.get(sid, {}).get("queue", []))

    async def clear_queue(self, sid):
        # Mirrors ServerRepository.clear_queue EXACTLY, currentTrack null
        # included. The older shim cleared only the queue, so a caller that
        # wrongly reused this teardown-shaped method looked correct under test
        # while production nulled currentTrack and tripped listener.py's
        # web-skip rule. A fake that quietly differs hides the bug it stands in
        # for; keep these two writes identical.
        self.states.setdefault(sid, {}).update({"queue": [], "currentTrack": None})

    async def pop_next_track(self, sid):
        queue = self.states.get(sid, {}).get("queue", [])
        return queue.pop(0) if queue else None

    async def set_session_code(self, sid, code):
        self.session_codes[sid] = code
        await self.update_state(sid, {"sessionCode": code})

    async def invalidate_session_code(self, sid):
        self.session_codes.pop(sid, None)
        if sid in self.states:
            self.states[sid]["sessionCode"] = None

    async def active_server_ids(self):
        return [sid for sid, s in self.states.items() if s.get("voiceChannelId")]

    async def save_history(self, sid, session_id, tracks, started, ended):
        self.history.append((sid, session_id, tracks))

    async def log_music(self, sid, track):
        self.music_log.append((sid, track))

    async def log_command(
        self, sid, command, args, user, user_id, *, source="discord", transcript=""
    ):
        self.command_log.append((sid, command, args, user, source, transcript))

    async def set_search_results(self, sid, results, playlist_name=None):
        await self.update_state(sid, {
            "searchResults": results, "searchQuery": None,
            "searchPlaylistName": playlist_name,
        })

    activated = True

    async def is_activated(self, sid):
        return self.activated_overrides.get(sid, self.activated)

    async def save_control_token(self, token_hash, data):
        self.control_tokens[token_hash] = data

    async def get_control_token(self, token_hash):
        return self.control_tokens.get(token_hash)

    async def delete_control_tokens_for_user(self, user_id):
        matches = [
            h for h, d in self.control_tokens.items() if d.get("userId") == user_id
        ]
        for h in matches:
            del self.control_tokens[h]
        return len(matches)

    async def touch_control_token(self, token_hash, iso_now):
        self.control_tokens.setdefault(token_hash, {})["lastUsed"] = iso_now


def make_track(title="Song", url="https://youtu.be/abc123", encoded="ENC1",
               identifier="abc123", length_ms=180000, author="Artist"):
    return {
        "encoded": encoded,
        "info": {
            "identifier": identifier, "title": title, "author": author,
            "length": length_ms, "uri": url, "artworkUrl": "",
        },
    }


class FakeNode:
    """Records update_player calls; serves canned load results."""

    def __init__(self) -> None:
        self.connected = True
        self.session_id = "fake-session"
        self.updates: list[tuple[int, dict]] = []
        self.destroyed: list[int] = []
        self.load_results: dict[str, LoadResult] = {}
        self.default_result = LoadResult(kind="track", tracks=[make_track()])
        self.fail_loads = False

    async def load_tracks(self, identifier):
        if self.fail_loads:
            raise RuntimeError("load failure (fake)")
        return self.load_results.get(identifier, self.default_result)

    async def update_player(self, guild_id, data, *, no_replace=False):
        self.updates.append((guild_id, data))
        return {}

    async def destroy_player(self, guild_id):
        self.destroyed.append(guild_id)


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        # Lets a test force the "Discord never received it" case, which the
        # announce actions must report as failure rather than success.
        self.fail = False

    async def send(self, guild_id, **kwargs):
        if self.fail:
            return False
        self.sent.append({"guild_id": guild_id, **kwargs})
        return True


@dataclass
class FakeVoice:
    channel: object = None
    disconnected: bool = False
    voice_resent: bool = True

    async def disconnect(self, *, force=False):
        self.disconnected = True

    async def resend_voice(self):
        return self.voice_resent


@dataclass
class FakeMe:
    nick: str | None = None

    async def edit(self, nick=None):
        self.nick = nick


@dataclass
class FakeVoiceState:
    channel: object = None


@dataclass
class FakeMember:
    id: int
    voice: FakeVoiceState | None = None
    display_name: str = "Tester"   # matches the token minted in the test fixture


@dataclass
class FakeVoiceChannel:
    id: int
    name: str = "General"
    guild: object = None

    async def connect(self, *, cls=None):
        voice = FakeVoice(channel=self)
        self.guild.voice_client = voice
        return voice


@dataclass
class FakeGuild:
    id: int
    voice_client: object = None
    me: FakeMe = field(default_factory=FakeMe)
    name: str = "Guild"
    icon: object = None
    channels: dict = field(default_factory=dict)
    members_by_id: dict = field(default_factory=dict)
    # Members reachable only over REST, i.e. NOT in discord.py's cache. The
    # bot runs without the privileged members intent, so the real cache holds
    # roughly the bot plus whoever is connected to voice — everyone else has
    # to be fetched. This dict reproduces that gap.
    rest_members_by_id: dict = field(default_factory=dict)
    voice_channels: list = field(default_factory=list)

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    def get_member(self, user_id):
        return self.members_by_id.get(user_id)

    async def fetch_member(self, user_id):
        member = self.members_by_id.get(user_id) or self.rest_members_by_id.get(user_id)
        if member is None:
            raise FakeNotFound(f"member {user_id} not found")
        return member

    def add_voice_channel(self, channel_id, name="General"):
        channel = FakeVoiceChannel(id=channel_id, name=name, guild=self)
        self.channels[channel_id] = channel
        self.voice_channels.append(channel)
        return channel


class FakeBot:
    def __init__(self) -> None:
        self.guilds: list[FakeGuild] = []
        # Read by the announce route's status branch (int(latency * 1000));
        # a real bot always has both. `node` is wired in the service fixture.
        self.latency = 0.0
        # get_cog mirrors discord.py: None for a cog that was never added.
        # The announce route reads Status.started_at through this and must
        # degrade (omit the uptime line) when it is absent.
        self.cogs: dict[str, object] = {}

    def get_cog(self, name):
        return self.cogs.get(name)

    def get_guild(self, guild_id):
        for g in self.guilds:
            if g.id == guild_id:
                return g
        return None

    def get_channel(self, channel_id):
        return None

    def is_ready(self):
        return True


@dataclass(frozen=True)
class FakeSettings:
    idle_timeout_seconds: int = 300
    empty_channel_timeout_seconds: int = 120
    web_app_url: str = "http://web.test"
    guardian_status_url: str = "http://guardian.test/status"


@pytest.fixture
def guild_id():
    return 123


@pytest.fixture
def sid(guild_id):
    return str(guild_id)


@pytest.fixture
async def service(guild_id):
    """A PlayerService over all-fake dependencies, with one active guild."""
    from jacky.audio.player import PlayerService
    from jacky.audio.provider import SingleNodeProvider

    repo = FakeRepo()
    node = FakeNode()
    notifier = FakeNotifier()
    bot = FakeBot()
    bot.guilds.append(FakeGuild(id=guild_id, voice_client=FakeVoice()))
    svc = PlayerService(
        bot, SingleNodeProvider(node), repo, FakeSettings(), notifier
    )
    await repo.init_state(str(guild_id))
    svc.search_retry_delay = 0
    svc.node = node
    svc.fake_notifier = notifier
    # The announce route's status branch reads the node off the BOT (as the
    # Status cog does), not off the service.
    bot.node = node
    yield svc
    for task in list(svc.idle_tasks.values()) + list(svc.empty_channel_tasks.values()):
        task.cancel()
