import asyncio
import json
import logging
import re
from types import SimpleNamespace
from urllib.parse import parse_qsl

import aiohttp
import pytest
from aiohttp import web

from guardian import act as act_module
from guardian.act import Actor, DockerClient
from guardian.alert import Alerter
from guardian.config import Settings

# Docker's /containers/json returns full 64-hex ids.
LAVALINK_ID = "a1" * 32
MINTER_ID = "b2" * 32
BOT_ID = "c3" * 32

# Mirrors the compose socket-proxy allow rule for POST (see the pinned-wire
# test below): anything else the fake answers 403, exactly like the proxy.
_RESTART_PATH = re.compile(r"/containers/([0-9a-f]{12,64})/restart")

# Deliberately NOT a discord.com URL: nothing here may look like a real secret.
FAKE_WEBHOOK = "https://webhook.invalid/api/webhooks/0/FAKE-TOKEN-not-a-secret"


class FakeDockerEngine:
    """The Docker Engine API as seen THROUGH the filtering socket proxy.

    Records every request line it receives and, like the proxy, refuses
    (403) everything except the two allowed requests.
    """

    def __init__(self):
        self.requests: list[tuple[str, str]] = []  # (method, raw request target)
        self.restarts: list[str] = []
        self.containers = {"lavalink": LAVALINK_ID, "token-minter": MINTER_ID, "bot": BOT_ID}

    def routes(self):
        return [web.route("*", "/{tail:.*}", self.dispatch)]

    async def dispatch(self, request):
        self.requests.append((request.method, request.raw_path))
        if request.method == "GET" and request.path == "/containers/json":
            return await self.list_containers(request)
        restart = _RESTART_PATH.fullmatch(request.path)
        if request.method == "POST" and restart:
            self.restarts.append(restart.group(1))
            return web.Response(status=204)
        return web.Response(status=403, text="blocked by socket-proxy allowlist")

    async def list_containers(self, request):
        filters = json.loads(request.query["filters"])
        service = next(
            lbl.split("=")[1] for lbl in filters["label"]
            if lbl.startswith("com.docker.compose.service=")
        )
        cid = self.containers.get(service)
        return web.json_response([{"Id": cid}] if cid is not None else [])


async def test_docker_restart_targets_service_by_label(serve, http_session):
    engine = FakeDockerEngine()
    base = await serve(engine.routes())
    docker = DockerClient(http_session, base_url=base)

    assert await docker.restart_service("lavalink")
    assert engine.restarts == [LAVALINK_ID]
    assert not await docker.restart_service("nonexistent")


async def test_docker_wire_requests_are_pinned_for_the_socket_proxy(serve, http_session):
    # PINNED WIRE CONTRACT. The socket-proxy service in deploy/docker-compose.yml
    # allows exactly these two request lines and denies everything else:
    #   GET  /containers/json                      (any query string)
    #   POST /containers/<12-64 hex>/restart       (any query string)
    # If this test has to change — a different method, a different path, an
    # API-version prefix such as /v1.43 — the compose socket-proxy allow rules
    # MUST change in the same commit, or guardian silently loses the ability
    # to restart anything (the proxy answers 403 and every restart "fails").
    seen: list[tuple[str, str]] = []

    async def permissive_recorder(request):
        # Answers yes to anything, so a drifted request shows up below as a
        # readable diff of the request line rather than as a bare False.
        seen.append((request.method, request.raw_path))
        if request.method == "GET":
            return web.json_response([{"Id": LAVALINK_ID}])
        return web.Response(status=204)

    base = await serve([web.route("*", "/{tail:.*}", permissive_recorder)])
    docker = DockerClient(http_session, base_url=base, project="jacky-music")

    assert await docker.restart_service("lavalink")

    assert len(seen) == 2, seen
    (list_method, list_target), restart_request = seen

    list_path, _, list_query = list_target.partition("?")
    assert (list_method, list_path) == ("GET", "/containers/json")
    params = parse_qsl(list_query, keep_blank_values=True, strict_parsing=True)
    assert [name for name, _ in params] == ["all", "filters"]
    assert params[0] == ("all", "true")
    assert json.loads(params[1][1]) == {
        "label": [
            "com.docker.compose.project=jacky-music",
            "com.docker.compose.service=lavalink",
        ]
    }

    assert restart_request == ("POST", f"/containers/{LAVALINK_ID}/restart?t=10")


@pytest.mark.parametrize(
    "bad_id",
    [
        "abc123",                                   # far too short
        "0123456789a",                              # 11: one under the short-id length
        "f" * 65,                                   # one over a full id
        "A1B2C3D4E5F6",                             # uppercase is not what Docker emits
        "g1h2i3j4k5l6",                             # not hex
        "0123456789ab/../../volumes/prune",         # path traversal
        "0123456789ab/kill?signal=KILL&x=",         # a different endpoint entirely
        LAVALINK_ID + "\n",                         # `$` would accept this; fullmatch must not
        123456789012,                               # not even a string
    ],
    ids=[
        "too-short", "eleven-chars", "sixty-five-chars", "uppercase", "not-hex",
        "path-traversal", "other-endpoint", "trailing-newline", "integer",
    ],
)
async def test_container_id_that_is_not_a_plain_hex_id_never_reaches_a_url(
    serve, http_session, caplog, bad_id
):
    engine = FakeDockerEngine()
    engine.containers["lavalink"] = bad_id
    base = await serve(engine.routes())
    docker = DockerClient(http_session, base_url=base)

    with caplog.at_level(logging.ERROR, logger="guardian.act"):
        assert not await docker.restart_service("lavalink")  # treated as "no container"

    # The id was never interpolated into a request: only the list call left.
    assert [method for method, _ in engine.requests] == ["GET"]
    assert engine.restarts == []
    assert "unusable container id" in caplog.text


@pytest.mark.parametrize(
    "listing",
    [{"message": "boom"}, "nope", [LAVALINK_ID], [{}], [{"Id": None}]],
    ids=["object-not-list", "string-not-list", "bare-id-entries", "entry-without-id", "null-id"],
)
async def test_malformed_container_listing_means_no_container_and_no_restart(
    serve, http_session, listing
):
    methods: list[str] = []

    async def engine(request):
        methods.append(request.method)
        if request.method == "GET":
            return web.json_response(listing)
        return web.Response(status=204)

    base = await serve([web.route("*", "/{tail:.*}", engine)])
    docker = DockerClient(http_session, base_url=base)

    assert not await docker.restart_service("lavalink")  # and it does not raise
    assert methods == ["GET"]


@pytest.mark.parametrize("good_id", ["0123456789ab", "f" * 64])
async def test_short_and_full_length_hex_ids_are_accepted(serve, http_session, good_id):
    engine = FakeDockerEngine()
    engine.containers["lavalink"] = good_id
    base = await serve(engine.routes())
    docker = DockerClient(http_session, base_url=base)

    assert await docker.restart_service("lavalink")
    assert engine.restarts == [good_id]


def _refuse_unix_socket(*args, **kwargs):
    raise AssertionError("opened the unix socket although DOCKER_API_URL is set")


async def test_docker_api_url_routes_calls_over_tcp_and_never_opens_the_unix_socket(
    serve, base_env, monkeypatch
):
    engine = FakeDockerEngine()
    base = await serve(engine.routes())
    base_env.setenv("DOCKER_API_URL", base)
    unix_connector = aiohttp.UnixConnector
    monkeypatch.setattr(aiohttp, "UnixConnector", _refuse_unix_socket)

    docker = DockerClient.from_settings(Settings.from_env())
    try:
        assert not isinstance(docker._session.connector, unix_connector)
        assert await docker.restart_service("lavalink")
        assert engine.restarts == [LAVALINK_ID]
    finally:
        await docker.close()


async def test_docker_defaults_to_the_unix_socket_when_api_url_is_unset(base_env):
    base_env.setenv("DOCKER_SOCKET", "/tmp/guardian-test.sock")

    docker = DockerClient.from_settings(Settings.from_env())
    try:
        connector = docker._session.connector
        assert isinstance(connector, aiohttp.UnixConnector)
        assert connector.path == "/tmp/guardian-test.sock"
    finally:
        await docker.close()


async def test_main_builds_its_docker_client_from_docker_api_url(base_env, monkeypatch):
    """The entrypoint itself must honour DOCKER_API_URL, not just the factory."""
    import guardian.__main__ as main_module

    base_env.setenv("DOCKER_API_URL", "http://socket-proxy.invalid:2375")
    monkeypatch.setattr(aiohttp, "UnixConnector", _refuse_unix_socket)

    built: list[DockerClient] = []

    class SpyActor(Actor):
        def __init__(self, docker):
            built.append(docker)
            super().__init__(docker)

    class _StatusRunner:
        async def cleanup(self):
            pass

    async def no_status_server(_guardian, _port):
        return _StatusRunner()

    async def shutdown_immediately(stop=None):
        # Returns without yielding, so the probe loops are cancelled before
        # their first step: main() runs end to end with no network I/O.
        return None

    monkeypatch.setattr(main_module, "Actor", SpyActor)
    monkeypatch.setattr(main_module, "start_status_server", no_status_server)
    monkeypatch.setattr(main_module, "wait_for_shutdown", shutdown_immediately)

    await main_module.main()

    (docker,) = built
    assert docker._base == "http://socket-proxy.invalid:2375"


@pytest.mark.parametrize("hang_on", ["list", "restart"])
async def test_hung_docker_endpoint_cannot_stall_the_caller(
    serve, http_session, monkeypatch, hang_on
):
    release = asyncio.Event()

    async def hang(_request):
        await release.wait()
        return web.Response(status=503)

    async def list_ok(_request):
        return web.json_response([{"Id": LAVALINK_ID}])

    base = await serve([
        web.get("/containers/json", hang if hang_on == "list" else list_ok),
        web.post("/containers/{cid}/restart", hang),
    ])
    monkeypatch.setattr(act_module, "DOCKER_TIMEOUT", aiohttp.ClientTimeout(total=0.2))
    actor = Actor(DockerClient(http_session, base_url=base))

    try:
        # The outer wait_for is only a safety net for THIS test: without the
        # per-call bound the client would sit on aiohttp's 300s session default.
        outcome = await asyncio.wait_for(actor.restart("lavalink"), timeout=5)
    finally:
        release.set()

    assert outcome == "failed"  # bounded, and still "never raises"


def test_docker_timeout_outlasts_the_stop_grace_but_stays_short():
    # Must exceed the `t=10` stop grace sent with the restart, and stay far
    # below the 120s probe interval so a hung proxy cannot stall a tick.
    assert 10 < act_module.DOCKER_TIMEOUT.total <= 20


async def test_actor_cooldown_blocks_restart_storms(serve, http_session, monkeypatch):
    engine = FakeDockerEngine()
    base = await serve(engine.routes())
    actor = Actor(DockerClient(http_session, base_url=base))

    assert await actor.restart("lavalink") == "restarted"
    assert await actor.restart("lavalink") == "cooldown"
    assert engine.restarts == [LAVALINK_ID]

    monkeypatch.setattr(act_module, "RESTART_COOLDOWN_SECONDS", 0.0)
    assert await actor.restart("lavalink") == "restarted"


async def test_actor_reports_failed_when_docker_unreachable(http_session):
    actor = Actor(DockerClient(http_session, base_url="http://127.0.0.1:1"))
    assert await actor.restart("lavalink") == "failed"  # no exception escapes


async def _capturing_webhook(serve):
    posts: list[dict] = []

    async def webhook(request):
        posts.append(await request.json())
        return web.Response(status=204)

    base = await serve([web.post("/hook", webhook)])
    return posts, f"{base}/hook"


def _fenced_detail(content: str) -> str:
    """The text the alert template placed between its own two code fences."""
    return content.split("\n```\n", 1)[1].rsplit("\n```", 1)[0]


async def test_alerter_playbook_message_cooldown_and_resolved(serve, http_session):
    posts, url = await _capturing_webhook(serve)
    alerter = Alerter(http_session, url)

    assert await alerter.alert("F2", "requires login")
    assert "make reauth" in posts[0]["content"]
    assert "requires login" in posts[0]["content"]

    # Cooldown: same playbook stays quiet; a different one still fires.
    assert not await alerter.alert("F2", "again")
    assert await alerter.alert("F4", "")
    assert len(posts) == 2

    # Resolved clears the cooldown and announces recovery.
    await alerter.resolved("F2")
    assert "[F2 resolved]" in posts[2]["content"]
    assert await alerter.alert("F2", "back again")


async def test_every_webhook_post_disables_mention_parsing(serve, http_session):
    posts, url = await _capturing_webhook(serve)
    alerter = Alerter(http_session, url)

    assert await alerter.alert("F2", "@everyone requires login <@&1> <@2>")
    await alerter.resolved("F2")
    await alerter.heartbeat()
    await alerter.info("release notes say @here")

    assert len(posts) == 4  # alert, resolved, heartbeat, info: every path
    for post in posts:
        assert post["allowed_mentions"] == {"parse": []}


@pytest.mark.parametrize(
    "detail",
    [
        "boom ``` @everyone",                 # exactly three
        "boom `````` six",                    # more than three
        "```\n**[F2] fake alert**\n```",       # tries to close the fence and reopen it
        "x" * 597 + "```",                    # run ends exactly at the 600-char cut
        "x" * 598 + "````",                   # run straddles the cut
        "x" * 599 + "```",                    # run starts on the last kept char
        "x" * 600 + "```",                    # run lies wholly past the cut
    ],
    ids=[
        "three", "six", "close-and-reopen",
        "ends-at-cut", "straddles-cut", "starts-on-last-kept-char", "past-cut",
    ],
)
async def test_detail_cannot_break_out_of_its_code_fence(serve, http_session, detail):
    posts, url = await _capturing_webhook(serve)
    alerter = Alerter(http_session, url)

    assert await alerter.alert("F4", detail)

    content = posts[0]["content"]
    assert content.count("```") == 2  # the template's own fences and nothing else
    assert "`" not in _fenced_detail(content)
    assert content.endswith("\n```\n_Runbook: docs/operations/RUNBOOK.md_")


async def test_backtick_runs_become_same_length_lookalikes_and_short_runs_survive(
    serve, http_session
):
    posts, url = await _capturing_webhook(serve)
    alerter = Alerter(http_session, url)

    assert await alerter.alert("F4", "a```b`````c use `make reauth` or ``x``")

    assert _fenced_detail(posts[0]["content"]) == "a'''b'''''c use `make reauth` or ``x``"


async def test_fence_run_straddling_the_truncation_is_neutralised_whole(serve, http_session):
    # Neutralise FIRST, truncate SECOND: a four-backtick run cut in half by the
    # 600-char limit must not survive as a two-backtick stub sitting directly
    # above the template's closing fence.
    posts, url = await _capturing_webhook(serve)
    alerter = Alerter(http_session, url)

    assert await alerter.alert("F4", "x" * 598 + "````" + "tail")

    assert _fenced_detail(posts[0]["content"]) == "x" * 598 + "''"


async def test_alerter_survives_broken_webhook(http_session):
    alerter = Alerter(http_session, "http://127.0.0.1:1/hook")
    assert not await alerter.alert("F4", "x")  # no exception raised


class _FailingSession:
    """A session whose every post fails the way aiohttp does: on entering."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def post(self, *_args, **_kwargs):
        return self

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *_exc_info):
        return False


@pytest.mark.parametrize(
    ("exc", "must_log"),
    [
        (aiohttp.InvalidURL(FAKE_WEBHOOK), ["InvalidURL"]),
        (
            aiohttp.ClientResponseError(
                SimpleNamespace(real_url=FAKE_WEBHOOK), (), status=404, message="Not Found"
            ),
            ["ClientResponseError", "404"],
        ),
    ],
    ids=["url-in-message", "url-and-http-status"],
)
async def test_webhook_url_never_reaches_the_log(caplog, exc, must_log):
    # Precondition: printing this exception really would leak the URL.
    assert FAKE_WEBHOOK in str(exc)
    alerter = Alerter(_FailingSession(exc), FAKE_WEBHOOK)

    with caplog.at_level(logging.DEBUG):
        assert not await alerter.alert("F4", "x")

    assert caplog.records, "the failure must still be logged"
    # caplog.text is the fully formatted output, tracebacks included.
    assert "webhook.invalid" not in caplog.text
    assert "FAKE-TOKEN" not in caplog.text
    for expected in must_log:
        assert expected in caplog.text


async def test_watcher_alerts_once_per_new_version(serve, http_session):
    from guardian.watcher import ReleaseWatcher

    async def latest(request):
        return web.json_response({"tag_name": "1.19.0"})

    base = await serve([web.get("/latest", latest)])
    infos: list[str] = []

    class FakeAlerter:
        async def info(self, text):
            infos.append(text)

    watcher = ReleaseWatcher(http_session, FakeAlerter(), pinned_version="1.18.1")
    import guardian.watcher as watcher_module
    original = watcher_module.RELEASES_URL
    watcher_module.RELEASES_URL = f"{base}/latest"
    try:
        await watcher.check()
        await watcher.check()  # second check: already notified, stays quiet
    finally:
        watcher_module.RELEASES_URL = original
    assert len(infos) == 1 and "1.19.0" in infos[0]
