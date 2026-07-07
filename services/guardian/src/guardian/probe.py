"""Probes: the guardian's only view of the world.

Every ~2 minutes:
- canary track lookup against Lavalink REST (exercises the full YouTube path)
- health ping to the bot (gateway-zombie detection, F5)
- frozen-position comparison between consecutive probes (silent playback, F6)
"""

import logging
from dataclasses import dataclass, field

import aiohttp

log = logging.getLogger("guardian.probe")

CANARY_TIMEOUT = aiohttp.ClientTimeout(total=25)
BOT_TIMEOUT = aiohttp.ClientTimeout(total=10)


@dataclass
class CanaryResult:
    reachable: bool          # Lavalink answered HTTP at all
    ok: bool                 # tracks actually came back
    error: str | None = None


@dataclass
class BotHealth:
    ok: bool
    players: dict = field(default_factory=dict)  # gid -> {position, playing, connected}


async def probe_canary(
    session: aiohttp.ClientSession, lavalink_url: str, password: str, query: str
) -> CanaryResult:
    from urllib.parse import quote

    url = f"{lavalink_url}/v4/loadtracks?identifier={quote(query, safe='')}"
    try:
        async with session.get(
            url, headers={"Authorization": password}, timeout=CANARY_TIMEOUT
        ) as resp:
            if resp.status >= 400:
                return CanaryResult(reachable=True, ok=False, error=f"HTTP {resp.status}")
            body = await resp.json(content_type=None)
    except Exception as exc:  # noqa: BLE001 — any transport failure is "unreachable"
        return CanaryResult(reachable=False, ok=False, error=str(exc))

    load_type = body.get("loadType")
    if load_type in ("search", "track", "playlist"):
        data = body.get("data")
        has_tracks = bool(data.get("tracks") if isinstance(data, dict) else data)
        if has_tracks:
            return CanaryResult(reachable=True, ok=True)
        return CanaryResult(reachable=True, ok=False, error="loadtracks returned no tracks")
    if load_type == "error":
        message = (body.get("data") or {}).get("message") or "unknown error"
        cause = (body.get("data") or {}).get("cause") or ""
        return CanaryResult(reachable=True, ok=False, error=f"{message} {cause}".strip())
    return CanaryResult(reachable=True, ok=False, error=f"empty result ({load_type})")


async def probe_bot(session: aiohttp.ClientSession, health_url: str) -> BotHealth:
    try:
        async with session.get(health_url, timeout=BOT_TIMEOUT) as resp:
            if resp.status != 200:
                return BotHealth(ok=False)
            body = await resp.json(content_type=None)
    except Exception as exc:  # noqa: BLE001
        log.debug("bot health probe failed: %s", exc)
        return BotHealth(ok=False)
    return BotHealth(ok=body.get("status") == "ok", players=body.get("players", {}))


def frozen_guilds(previous: dict, current: dict) -> list[str]:
    """Guilds claiming to play that are truly stalled since the last probe.

    A stalled player reports the SAME track at the EXACT same position
    forever. Position regressions are healthy (track change, skip, loop
    restart, flap recovery) and previously caused false F6 restarts when
    two probes each caught a different track at a lower position.
    """
    frozen = []
    for gid, now in current.items():
        if not (now.get("playing") and now.get("connected")):
            continue
        before = previous.get(gid)
        if before is None or not before.get("playing"):
            continue
        if (
            now.get("trackId") == before.get("trackId")
            and now.get("position", 0) == before.get("position", 0)
        ):
            frozen.append(gid)
    return frozen
