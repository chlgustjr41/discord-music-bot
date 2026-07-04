import json

from aiohttp import web

from guardian import act as act_module
from guardian.act import Actor, DockerClient
from guardian.alert import Alerter


class FakeDockerEngine:
    def __init__(self):
        self.restarts: list[str] = []
        self.containers = {"lavalink": "abc123", "token-minter": "def456", "bot": "789"}

    def routes(self):
        return [
            web.get("/containers/json", self.list_containers),
            web.post("/containers/{cid}/restart", self.restart),
        ]

    async def list_containers(self, request):
        filters = json.loads(request.query["filters"])
        service = next(
            lbl.split("=")[1] for lbl in filters["label"]
            if lbl.startswith("com.docker.compose.service=")
        )
        cid = self.containers.get(service)
        return web.json_response([{"Id": cid}] if cid else [])

    async def restart(self, request):
        self.restarts.append(request.match_info["cid"])
        return web.Response(status=204)


async def test_docker_restart_targets_service_by_label(serve, http_session):
    engine = FakeDockerEngine()
    base = await serve(engine.routes())
    docker = DockerClient(http_session, base_url=base)

    assert await docker.restart_service("lavalink")
    assert engine.restarts == ["abc123"]
    assert not await docker.restart_service("nonexistent")


async def test_actor_cooldown_blocks_restart_storms(serve, http_session, monkeypatch):
    engine = FakeDockerEngine()
    base = await serve(engine.routes())
    actor = Actor(DockerClient(http_session, base_url=base))

    assert await actor.restart("lavalink") == "restarted"
    assert await actor.restart("lavalink") == "cooldown"
    assert engine.restarts == ["abc123"]

    monkeypatch.setattr(act_module, "RESTART_COOLDOWN_SECONDS", 0.0)
    assert await actor.restart("lavalink") == "restarted"


async def test_actor_reports_failed_when_docker_unreachable(http_session):
    actor = Actor(DockerClient(http_session, base_url="http://127.0.0.1:1"))
    assert await actor.restart("lavalink") == "failed"  # no exception escapes


async def test_alerter_playbook_message_cooldown_and_resolved(serve, http_session):
    posts: list[dict] = []

    async def webhook(request):
        posts.append(await request.json())
        return web.Response(status=204)

    base = await serve([web.post("/hook", webhook)])
    alerter = Alerter(http_session, f"{base}/hook")

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


async def test_alerter_survives_broken_webhook(http_session):
    alerter = Alerter(http_session, "http://127.0.0.1:1/hook")
    assert not await alerter.alert("F4", "x")  # no exception raised


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
