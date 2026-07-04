# jacky-token-minter

**What:** Keeps the poToken auth layer fresh: asks the pot-provider sidecar
for a new poToken/visitorData every `POT_REFRESH_HOURS`, pushes them to
Lavalink at runtime (`POST /youtube`, no restart), and persists them to the
tokens volume for Lavalink cold starts. Contract with pot-provider is
pinned in ADR-0004.

**Status:** Active since M2.

**Run:** `pip install -e ".[dev]" && python -m minter` · Tests: `pytest`

**Depends on:** pot-provider (`POT_PROVIDER_URL`), Lavalink REST
(`LAVALINK_HOST/PORT/PASSWORD`), tokens volume (`TOKENS_FILE`).
