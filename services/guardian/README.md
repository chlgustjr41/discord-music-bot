# jacky-guardian

**What:** The supervisor. Probes (canary track lookup + bot ping every
2 min), classifies failures to playbook IDs F1–F9, restarts sick containers
(Docker socket), alerts via Discord webhook with the exact fix.

**Run:** `pip install -e ".[dev]" && python -m guardian` · Tests: `pytest`

**Depends on:** Lavalink REST, bot health endpoint, Docker socket
(mounted), ALERT_WEBHOOK_URL. One module per duty: `probe` / `classify` /
`act` / `alert`.
