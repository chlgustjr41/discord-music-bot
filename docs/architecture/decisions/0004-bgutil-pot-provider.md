# ADR-0004: bgutil pot-provider replaces the Chromium session generator

**Status:** Accepted · 2026-07-03 · Supersedes the mint mechanism of ADR-0002

## Context
The design spec (§3.3) assumed a headless-Chromium "trusted session
generator" one-shot (~400MB spikes). That tool (iv-org) is deprecated and
unreliable against current YouTube. The actively maintained alternative,
bgutil-ytdlp-pot-provider, solves the BotGuard attestation in Node without
a browser.

## Decision
Run the stock `brainicism/bgutil-ytdlp-pot-provider:node` image as an
always-on sidecar (`pot-provider`, internal port 4416), and our own small
Python `token-minter` service that periodically requests tokens from it,
pushes them to Lavalink's `POST /youtube`, and persists them to the tokens
volume for cold starts.

## Pinned contract (empirical, 2026-07-03, server v1.3.1)

Probed against a running `brainicism/bgutil-ytdlp-pot-provider:node`
container and confirmed against the server source inside the image
(`/app/build/main.js`, `/app/build/session_manager.js`).

### `POST /get_pot`
- **Request:** JSON object. All fields optional; `{}` is valid — with no
  `content_binding` the server generates fresh visitor data via Innertube
  and mints a poToken bound to it.
  - Accepted fields (snake_case): `content_binding`, `proxy`,
    `bypass_cache`, `source_address`, `disable_tls_verification`,
    `challenge`, `innertube_context`.
  - Deprecated fields that now return HTTP 400: `visitor_data`,
    `data_sync_id`, `disable_innertube`. (Do NOT send `visitor_data` —
    the request and response conventions differ.)
- **Response (200):** camelCase, e.g.
  `{"contentBinding": "<visitorData>", "poToken": "<token>",
  "expiresAt": "2026-07-04T03:07:30.122Z"}`
  - `contentBinding` IS the visitorData: URL-encoded Innertube protobuf
    (`CgtRNTBsVnBiQW15OCi...%3D%3D`, ~700 chars) — note it contains `%`.
  - `poToken` is a websafe-base64 string (`A-Za-z0-9_-`, `=` padding,
    ~800 chars), minted via `mintAsWebsafeString`.
  - `expiresAt` = mint time + `TOKEN_TTL` hours (env var, default 6).
- **Errors:** HTTP 500 with `{"error": "<message>"}` on mint failure.
- **Timing:** 16.7s (first mint) and 13.1s (second) measured. Each
  empty-body call mints a fresh visitorData+poToken pair — the server's
  cache is keyed by content binding, so bodiless requests never hit it.
  Budget ≥30s per request; the minter uses a 120s client timeout.

### Other routes
- `GET /ping` → 200 `{"server_uptime": <seconds>, "version": "1.3.1"}`
  (~30ms) — suitable for a container healthcheck.
- `POST /invalidate_caches` → 204; `POST /invalidate_it` → 204;
  `GET /minter_cache` → 200 JSON array of cache keys.

### Push to Lavalink (verified end-to-end)
`POST /youtube` on Lavalink with
`{"poToken": <poToken>, "visitorData": <contentBinding>}` and the standard
`Authorization` header returned **204** in ~0.2s — the minted pair
(including the `%`-encoded visitorData) is accepted verbatim; no decoding
or re-encoding step is needed.

### Lavalink image tooling (for the compose healthcheck)
The lavalink image has **both** `curl` (`/usr/bin/curl`) and `wget`
(`/usr/bin/wget`).

## Consequences
(+) No Chromium: steady-state RAM ~70MiB instead of 400MB spikes; stock
image means upstream maintains the BotGuard arms race, not us.
(−) One more always-on container; the provider's API is pinned by this ADR
rather than upstream docs — if a bgutil upgrade changes it, the minter's
contract tests catch it.
(−) Response field names (`poToken`/`contentBinding`) differ from the
yt-dlp GetPOT convention the plan assumed (`po_token`/`visitor_data`);
the minter's code and fake-server tests follow this ADR.
