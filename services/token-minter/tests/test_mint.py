import aiohttp
import pytest
from aiohttp import web

from minter.mint import MintError, fetch_tokens, push_to_lavalink, write_tokens_file


async def test_fetch_tokens_happy_path(serve) -> None:
    # Response shape pinned by ADR-0004: camelCase fields, contentBinding is
    # the (URL-encoded, %-containing) visitorData.
    async def get_pot(request: web.Request) -> web.Response:
        assert await request.json() == {}
        return web.json_response(
            {
                "contentBinding": "VD456%3D%3D",
                "poToken": "PO123",
                "expiresAt": "2026-07-04T03:07:30.122Z",
            }
        )

    url = await serve([web.post("/get_pot", get_pot)])
    async with aiohttp.ClientSession() as session:
        po, vd = await fetch_tokens(session, url)
    assert (po, vd) == ("PO123", "VD456%3D%3D")


async def test_fetch_tokens_http_error_raises(serve) -> None:
    async def get_pot(request: web.Request) -> web.Response:
        # ADR-0004: mint failures surface as HTTP 500 {"error": msg}.
        return web.json_response({"error": "boom"}, status=500)

    url = await serve([web.post("/get_pot", get_pot)])
    async with aiohttp.ClientSession() as session:
        with pytest.raises(MintError):
            await fetch_tokens(session, url)


async def test_fetch_tokens_missing_fields_raises(serve) -> None:
    async def get_pot(request: web.Request) -> web.Response:
        return web.json_response({"unexpected": True})

    url = await serve([web.post("/get_pot", get_pot)])
    async with aiohttp.ClientSession() as session:
        with pytest.raises(MintError):
            await fetch_tokens(session, url)


async def test_push_sends_auth_and_accepts_204(serve) -> None:
    seen: dict = {}

    async def youtube(request: web.Request) -> web.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = await request.json()
        return web.Response(status=204)

    url = await serve([web.post("/youtube", youtube)])
    async with aiohttp.ClientSession() as session:
        await push_to_lavalink(session, url, "hunter2", "PO123", "VD456")
    assert seen["auth"] == "hunter2"
    assert seen["body"] == {"poToken": "PO123", "visitorData": "VD456"}


async def test_push_non_204_raises(serve) -> None:
    async def youtube(request: web.Request) -> web.Response:
        return web.Response(status=401)

    url = await serve([web.post("/youtube", youtube)])
    async with aiohttp.ClientSession() as session:
        with pytest.raises(MintError):
            await push_to_lavalink(session, url, "wrong", "PO123", "VD456")


def test_write_tokens_file_atomic(tmp_path) -> None:
    path = tmp_path / "sub" / "tokens.env"
    write_tokens_file(str(path), "PO-abc_123=", "VD_def-456%3D%3D")
    assert path.read_text() == "POT_TOKEN=PO-abc_123=\nPOT_VISITOR_DATA=VD_def-456%3D%3D\n"
    assert not path.with_suffix(".env.tmp").exists()


def test_write_tokens_file_rejects_unsafe_values(tmp_path) -> None:
    with pytest.raises(MintError):
        write_tokens_file(str(tmp_path / "t.env"), "evil\ntoken", "VD")
    with pytest.raises(MintError):
        write_tokens_file(str(tmp_path / "t.env"), "PO", 'VD"quoted')


def test_write_tokens_file_rejects_sed_specials(tmp_path) -> None:
    # '/' and '+' would break the sed expression the values are injected into;
    # per ADR-0004 neither appears in websafe/URL-encoded values, so reject.
    with pytest.raises(MintError):
        write_tokens_file(str(tmp_path / "t.env"), "PO/slash", "VD")
    with pytest.raises(MintError):
        write_tokens_file(str(tmp_path / "t.env"), "PO", "VD+plus")
