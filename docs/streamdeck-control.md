# Stream Deck Session Control — Setup & Runbook

Spec: `superpowers/specs/2026-08-06-streamdeck-session-control-design.md`
Plugin source: `streamdeck-plugin/` · Bot API: `services/bot/src/jacky/api/control.py`

## One-time server setup

1. **Token** — on the VM, in `deploy/.env`:
   `CONTROL_API_TOKEN=$(openssl rand -hex 32)` (paste the value, keep it secret).
2. **Cloudflare tunnel** (requires a domain on your Cloudflare account):
   - Zero Trust → Networks → Tunnels → Create tunnel → Cloudflared connector.
   - From the "Install connector" Docker snippet, copy the long token value
     (shown after `--token`) into `CLOUDFLARE_TUNNEL_TOKEN` in `deploy/.env`
     (value only — the compose file passes it via the `TUNNEL_TOKEN` env var).
   - Public hostname: subdomain `control`, your domain; **Path: `control`**
     (only `/control/*` is forwarded — `/health` stays private); service
     `http://bot:8080` (HTTP — TLS terminates at Cloudflare).
3. **Enable the sidecar** — uncomment `COMPOSE_PROFILES=control` in `deploy/.env`.
4. Redeploy the stack (`make up` on the VM). `docker compose ps` should show
   `cloudflared` running; its logs print `Registered tunnel connection`.

### Failure signature: empty/missing tunnel token

With the profile enabled but `CLOUDFLARE_TUNNEL_TOKEN` empty, cloudflared
starts and **crash-loops** under `restart: unless-stopped` (logs show a
token/origin-cert error). There is no up-front validation — compose profiles
cannot express per-profile required variables — so a crash-looping
`cloudflared` in `docker compose ps` means: check the token in `deploy/.env`.

## Verify from anywhere

```bash
curl -s https://control.<your-domain>/control/now-playing?discordUserId=<your-id>
# → {"error": "unauthorized"} (401) — tunnel + routing work
curl -s -H "Authorization: Bearer <token>" \
  "https://control.<your-domain>/control/now-playing?discordUserId=<your-id>"
# → {"active": false} (or track info if you're in a session)
```

Your Discord user ID: Discord → Settings → Advanced → Developer Mode on,
then right-click your name → Copy User ID.

## Install / update the plugin

From `streamdeck-plugin/`: `npm install && npm run build`, then
`npx @elgato/cli pack com.jacobchoi.jacky-control.sdPlugin` and double-click
the produced `.streamDeckPlugin` file (or `npx @elgato/cli link
com.jacobchoi.jacky-control.sdPlugin` for a dev symlink + `npm run watch`).

In the Stream Deck app, drop any Jacky action onto a key and fill the three
settings (shared by all keys): **API URL** `https://control.<your-domain>`,
**API token**, **Discord user ID**.

## Behavior notes

- Keys act on the guild where *you* currently sit in a voice channel with a
  live bot session; nowhere → "No session" / brief ⚠ flash on presses.
- Now Playing polls every 5 s, backing off to 30 s while unreachable.
- Token rotation: new value in `deploy/.env` → `make restart s=bot` → update
  the token in any key's settings.
