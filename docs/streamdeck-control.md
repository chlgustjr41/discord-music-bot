# Stream Deck Session Control — Setup & Runbook

Spec: `superpowers/specs/2026-08-06-streamdeck-session-control-design.md`
Plugin source: `streamdeck-plugin/` · Bot API: `services/bot/src/jacky/api/control.py`

## One-time server setup

1. **Discord OAuth app** — Discord Developer Portal → Applications → your bot's app → OAuth2:
   - Copy the **Client ID** into `DISCORD_CLIENT_ID` in `deploy/.env`.
   - Click "Reset Secret", copy the new secret into `DISCORD_CLIENT_SECRET` in `deploy/.env`.
   - Add a redirect URI: `https://control.<your-domain>/control/auth/callback` (replace `<your-domain>` with your actual domain).
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
# No-auth check: tunnel + routing work
curl -s "https://control.<your-domain>/control/now-playing"
# → {"error":"unauthorized"} (401)

# With auth: start an OAuth flow to get a token
curl -s -X POST "https://control.<your-domain>/control/auth/start"
# → JSON containing "state" and "authorizeUrl"
# (User tokens are minted via the plugin's sign-in flow, not curl — for testing,
# grep `controlTokens` in Firestore or use the plugin to sign in first.)
```

## Install / update the plugin

From `streamdeck-plugin/`: `npm install && npm run build`, then
`npx @elgato/cli pack com.jacobchoi.jacky-control.sdPlugin` and double-click
the produced `.streamDeckPlugin` file (or `npx @elgato/cli link
com.jacobchoi.jacky-control.sdPlugin` for a dev symlink + `npm run watch`).

In the Stream Deck app, drop any Jacky action on a key and click **Sign in with Discord** — no URL, token, or user ID to type. (API URL remains as an advanced override for non-default domains.)

## Onboarding a friend

1. **Install the plugin** — give them the `.streamDeckPlugin` file from your build.
2. **Drag a key** — drop any Jacky action onto one of their Stream Deck keys.
3. **Sign in** — they'll see a **Sign in with Discord** button in the Property Inspector. Clicking it opens their browser to Discord's consent screen (which names your bot app).
4. **Server requirement** — they must be a member of at least one server where the bot is activated. If not, the sign-in will fail with "not a member of any server Jacky serves."

Once signed in, their authentication persists across restarts and key changes.

## Behavior notes

- Keys act on the guild where *you* currently sit in a voice channel with a
  live bot session; nowhere → "No session" / brief ⚠ flash on presses.
- Now Playing polls every 5 s, backing off to 30 s while unreachable.
- Token revocation: run `j!unlink` in any activated server to revoke all your Stream Deck sign-ins (one command, all devices). Sign in again anytime. Env changes (`make up`) still apply for new deployments.

## Known limitations / follow-ups

- Summon key icon state (joined/left) comes from press responses only — can go stale if presses fail silently or the session changes mid-flight.
- **Sign-in device-phishing caveat:** if someone convinces you to click *their* authorize link (not one you initiated in your Stream Deck), they can capture a token for your identity. Discord's consent screen names the app; only authorize links you initiated yourself.
- Pending sign-in cap: ~200 concurrent sessions (in-memory, per-bot). Hitting the cap will reject new `POST /control/auth/start` calls until older ones time out (10 min) or complete.
- `@elgato/streamdeck` 1.x is npm-deprecated in favor of 2.x (Marketplace-only
  concern; personal install unaffected). Revisit if Marketplace publishing or
  security patches ever matter.
- Play/Pause key keeps its last icon while offline / session-less (presses
  still flash ⚠ honestly).
- The Now Playing `⏸` glyph may render as a missing-glyph box on some
  Stream Deck firmware fonts — swap for `||` in
  `streamdeck-plugin/src/actions/now-playing.ts` if it looks wrong.
- The marquee advances 2 characters per poll tick (every 5 s) — slow scroll
  is by design (poll-driven), not a bug.
- Plugin tests are not wired into `make test` (services/ only); run them with
  `cd streamdeck-plugin && npm test`.
