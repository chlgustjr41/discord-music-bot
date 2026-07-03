# lavalink

**What:** Lavalink v4 + youtube-source plugin — the audio engine.

**Config:** `application.yml.tmpl` rendered at container start; the plugin
version comes ONLY from `.env` (`YOUTUBE_PLUGIN_VERSION`) so config/jar
drift is impossible. Secrets resolve via Spring env placeholders — never
written to disk.

**Depends on:** YouTube (poToken via token-minter volume + OAuth env +
client ordering — see ADR-0002).
