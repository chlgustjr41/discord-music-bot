# jacky-guardian

**What:** The supervisor. Probes (canary track lookup + bot ping every
2 min), classifies failures to playbook IDs F1–F9, restarts sick containers
(through the `socket-proxy` service), alerts via Discord webhook with the
exact fix.

**Status:** production.

**Run:** `pip install -e ".[dev]" && python -m guardian` · Tests: `pytest`

**Depends on:** Lavalink REST, bot health endpoint, the Docker API via
`DOCKER_API_URL` (the filtering socket proxy; in the compose stack it never
mounts the socket itself), ALERT_WEBHOOK_URL. One module per duty: `probe` / `classify` /
`act` / `alert`.
