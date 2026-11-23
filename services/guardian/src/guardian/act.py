"""Actions: restart sick containers through the Docker Engine API.

The guardian is the ONLY service with Docker access (spec §3.4). Restarts
target compose services by label, so container names/scaling never matter.
A per-service cooldown stops restart storms: if a restart didn't cure it,
the next escalation is a human alert, not another restart.

Transport: with DOCKER_API_URL set, plain TCP to the filtering socket proxy
(which allows exactly the two requests built below and nothing else); with
it unset, the mounted unix socket (root-equivalent on the host — legacy).
"""

import json
import logging
import re
import time
from urllib.parse import quote

import aiohttp

from guardian.config import Settings

log = logging.getLogger("guardian.act")

RESTART_COOLDOWN_SECONDS = 600.0

# Total bound per Docker call. Has to outlast the `t=10` stop grace sent with
# a restart, and stay far below the probe interval: without it a hung proxy
# would hold a probe tick for aiohttp's 300s session default.
DOCKER_TIMEOUT = aiohttp.ClientTimeout(total=15)

# What Docker emits (12-char short id .. 64-char full id) and, deliberately,
# the same shape the socket-proxy allow rule for restart accepts. Anything
# else is never interpolated into a request path.
_CONTAINER_ID = re.compile(r"[0-9a-f]{12,64}")


class DockerClient:
    """Minimal Docker Engine API client.

    `base_url` is injectable so tests run it against a TCP fake; production
    picks the transport with `from_settings`.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str = "http://localhost",
        project: str = "jacky-music",
    ) -> None:
        self._session = session
        self._base = base_url.rstrip("/")
        self._project = project

    @classmethod
    def from_settings(cls, settings: Settings) -> "DockerClient":
        """TCP (socket proxy) when DOCKER_API_URL is set, else the unix socket."""
        if settings.docker_api_url:
            return cls.for_url(settings.docker_api_url, settings.compose_project)
        return cls.for_socket(settings.docker_socket, settings.compose_project)

    @classmethod
    def for_url(cls, base_url: str, project: str) -> "DockerClient":
        """Plain TCP to `base_url`. Never opens the unix socket."""
        return cls(aiohttp.ClientSession(), base_url=base_url, project=project)

    @classmethod
    def for_socket(cls, socket_path: str, project: str) -> "DockerClient":
        connector = aiohttp.UnixConnector(path=socket_path)
        session = aiohttp.ClientSession(connector=connector)
        return cls(session, project=project)

    async def close(self) -> None:
        await self._session.close()

    # ---- WIRE CONTRACT -------------------------------------------------------
    # The socket-proxy service in deploy/docker-compose.yml allowlists exactly
    # the two request lines built below (unversioned: no /v1.xx prefix):
    #   GET  /containers/json?all=true&filters=<json>
    #   POST /containers/<id>/restart?t=<seconds>
    # Changing a method or a path here means changing the compose socket-proxy
    # allow rules IN THE SAME COMMIT — otherwise the proxy answers 403 and
    # guardian silently loses the ability to restart anything. Pinned by
    # test_docker_wire_requests_are_pinned_for_the_socket_proxy.
    # --------------------------------------------------------------------------

    def _list_url(self, service: str) -> str:
        filters = json.dumps({
            "label": [
                f"com.docker.compose.project={self._project}",
                f"com.docker.compose.service={service}",
            ]
        })
        return f"{self._base}/containers/json?all=true&filters={quote(filters, safe='')}"

    def _restart_url(self, container_id: str, timeout_seconds: int) -> str:
        return f"{self._base}/containers/{container_id}/restart?t={timeout_seconds}"

    async def _find_container(self, service: str) -> str | None:
        async with self._session.get(self._list_url(service), timeout=DOCKER_TIMEOUT) as resp:
            if resp.status != 200:
                log.error("docker list containers -> HTTP %s", resp.status)
                return None
            containers = await resp.json(content_type=None)
        if not isinstance(containers, list) or not containers:
            return None
        first = containers[0]
        container_id = first.get("Id") if isinstance(first, dict) else None
        if not isinstance(container_id, str) or not _CONTAINER_ID.fullmatch(container_id):
            # The id goes straight into a request path: a value that is not a
            # plain hex id is refused rather than escaped. Same outcome as "no
            # container found" — the restart reports failed and a human is told.
            log.error(
                "docker returned an unusable container id for compose service '%s': %.80r",
                service, container_id,
            )
            return None
        return container_id

    async def restart_service(self, service: str, timeout_seconds: int = 10) -> bool:
        container_id = await self._find_container(service)
        if not container_id:
            log.error("no container found for compose service '%s'", service)
            return False
        url = self._restart_url(container_id, timeout_seconds)
        async with self._session.post(url, timeout=DOCKER_TIMEOUT) as resp:
            ok = resp.status == 204
            if not ok:
                log.error("docker restart %s -> HTTP %s", service, resp.status)
            return ok


class Actor:
    """Cooldown-guarded restart wrapper around DockerClient."""

    def __init__(self, docker: DockerClient) -> None:
        self._docker = docker
        self._last_restart: dict[str, float] = {}

    def _in_cooldown(self, service: str) -> bool:
        last = self._last_restart.get(service)
        return last is not None and (time.monotonic() - last) < RESTART_COOLDOWN_SECONDS

    async def restart(self, service: str) -> str:
        """Returns 'restarted' | 'cooldown' | 'failed'. Never raises: a broken
        or hung Docker endpoint must not abort the probe tick that requested
        the restart (a DOCKER_TIMEOUT expiry lands here as TimeoutError)."""
        if self._in_cooldown(service):
            return "cooldown"
        self._last_restart[service] = time.monotonic()
        try:
            ok = await self._docker.restart_service(service)
        except Exception as exc:  # noqa: BLE001
            # The type is logged too: str(TimeoutError()) is empty.
            log.error("restart %s failed: %s: %s", service, type(exc).__name__, exc)
            return "failed"
        return "restarted" if ok else "failed"
