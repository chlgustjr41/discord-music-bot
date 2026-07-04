"""Actions: restart sick containers over the Docker socket.

The guardian is the ONLY service with Docker access (spec §3.4). Restarts
target compose services by label, so container names/scaling never matter.
A per-service cooldown stops restart storms: if a restart didn't cure it,
the next escalation is a human alert, not another restart.
"""

import logging
import time

import aiohttp

log = logging.getLogger("guardian.act")

RESTART_COOLDOWN_SECONDS = 600.0


class DockerClient:
    """Minimal Docker Engine API client over the mounted socket.

    `base_url`/`connector` are injectable so tests run it against a TCP
    fake; production uses the unix socket.
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
    def for_socket(cls, socket_path: str, project: str) -> "DockerClient":
        connector = aiohttp.UnixConnector(path=socket_path)
        session = aiohttp.ClientSession(connector=connector)
        return cls(session, project=project)

    async def close(self) -> None:
        await self._session.close()

    async def _find_container(self, service: str) -> str | None:
        import json
        from urllib.parse import quote

        filters = json.dumps({
            "label": [
                f"com.docker.compose.project={self._project}",
                f"com.docker.compose.service={service}",
            ]
        })
        url = f"{self._base}/containers/json?all=true&filters={quote(filters, safe='')}"
        async with self._session.get(url) as resp:
            if resp.status != 200:
                log.error("docker list containers -> HTTP %s", resp.status)
                return None
            containers = await resp.json(content_type=None)
        return containers[0]["Id"] if containers else None

    async def restart_service(self, service: str, timeout_seconds: int = 10) -> bool:
        container_id = await self._find_container(service)
        if not container_id:
            log.error("no container found for compose service '%s'", service)
            return False
        url = f"{self._base}/containers/{container_id}/restart?t={timeout_seconds}"
        async with self._session.post(url) as resp:
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
        Docker socket must not abort the probe tick that requested the restart."""
        if self._in_cooldown(service):
            return "cooldown"
        self._last_restart[service] = time.monotonic()
        try:
            ok = await self._docker.restart_service(service)
        except Exception as exc:  # noqa: BLE001
            log.error("restart %s failed: %s", service, exc)
            return "failed"
        return "restarted" if ok else "failed"
