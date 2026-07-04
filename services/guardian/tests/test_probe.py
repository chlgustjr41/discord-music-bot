from aiohttp import web

from guardian.probe import frozen_guilds, probe_bot, probe_canary


async def test_canary_ok_on_search_results(serve, http_session):
    async def loadtracks(request):
        assert request.headers["Authorization"] == "pw"
        return web.json_response({"loadType": "search", "data": [{"encoded": "E"}]})

    url = await serve([web.get("/v4/loadtracks", loadtracks)])
    result = await probe_canary(http_session, url, "pw", "ytsearch:x")
    assert result.reachable and result.ok


async def test_canary_error_surfaces_message_and_cause(serve, http_session):
    async def loadtracks(request):
        return web.json_response({
            "loadType": "error",
            "data": {"message": "Sign in to confirm you're not a bot", "cause": "WEB"},
        })

    url = await serve([web.get("/v4/loadtracks", loadtracks)])
    result = await probe_canary(http_session, url, "pw", "ytsearch:x")
    assert result.reachable and not result.ok
    assert "Sign in to confirm" in result.error


async def test_canary_empty_results_is_failure(serve, http_session):
    async def loadtracks(request):
        return web.json_response({"loadType": "search", "data": []})

    url = await serve([web.get("/v4/loadtracks", loadtracks)])
    result = await probe_canary(http_session, url, "pw", "ytsearch:x")
    assert result.reachable and not result.ok


async def test_canary_unreachable(http_session):
    result = await probe_canary(http_session, "http://127.0.0.1:1", "pw", "ytsearch:x")
    assert not result.reachable and not result.ok


async def test_canary_http_500_is_reachable_failure(serve, http_session):
    async def loadtracks(request):
        return web.Response(status=500)

    url = await serve([web.get("/v4/loadtracks", loadtracks)])
    result = await probe_canary(http_session, url, "pw", "ytsearch:x")
    assert result.reachable and not result.ok and "500" in result.error


async def test_bot_health_ok_and_down(serve, http_session):
    async def health(request):
        return web.json_response({
            "status": "ok", "guilds": 1,
            "players": {"123": {"position": 5000, "playing": True, "connected": True}},
        })

    url = await serve([web.get("/health", health)])
    result = await probe_bot(http_session, f"{url}/health")
    assert result.ok and result.players["123"]["position"] == 5000

    down = await probe_bot(http_session, "http://127.0.0.1:1/health")
    assert not down.ok


def test_frozen_guilds_detects_stalled_position_only_when_playing():
    prev = {
        "1": {"position": 5000, "playing": True, "connected": True},
        "2": {"position": 5000, "playing": True, "connected": True},
        "3": {"position": 5000, "playing": False, "connected": True},
    }
    curr = {
        "1": {"position": 5000, "playing": True, "connected": True},   # frozen
        "2": {"position": 9000, "playing": True, "connected": True},   # advancing
        "3": {"position": 5000, "playing": False, "connected": True},  # paused: fine
        "4": {"position": 0, "playing": True, "connected": True},      # new: no baseline
    }
    assert frozen_guilds(prev, curr) == ["1"]
