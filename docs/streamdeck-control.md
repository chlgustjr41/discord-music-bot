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

> **Sign-in must be started from the Stream Deck itself.** The button opens
> the browser on the same machine, and the server requires the browser that
> finishes sign-in to come from the same address that started it. If someone
> sends you a "sign in to Jacky Music" link, it will be rejected
> ("Sign-in started somewhere else") — that link would have handed *them* a
> token carrying *your* identity. Never complete a sign-in link you didn't
> start yourself.
>
> Same-address binding also means an unusual network setup (plugin and
> browser leaving via different addresses, e.g. a VPN on one but not the
> other) can block a legitimate sign-in. If that happens, disable the VPN or
> proxy for the sign-in and retry.

## Behavior notes

- Keys act on the guild where *you* currently sit in a voice channel with a
  live bot session; nowhere → "No session" / brief ⚠ flash on presses.
  (Summon and Play Playlist are the exceptions — they act on the server
  configured on that specific key.)
- **Voice Command** key: hold it, speak, release. Wait for "Listening…" before
  speaking — opening the microphone takes about a second, so anything said
  before that is lost. Speech is interpreted by an LLM, so phrasing is
  flexible and several instructions in one breath work ("skip this, then add
  two songs by Radiohead").

  | Say | Result |
  |---|---|
  | `play <song>` — or just say the song | **Interrupts** and plays it now |
  | `play <song> next` | Front of the queue, without interrupting |
  | `add <song>` / `queue <song>` | Appends to the end |
  | `play` / `add playlist <name>` | Same three placements, for a saved playlist |
  | `skip`, `skip two` | Skip one or several |
  | `pause`, `resume`, `stop the music` | Stop-like speech **pauses** |
  | `louder` / `quieter`, `volume 40` | Relative or absolute |
  | `shuffle`, `clear the queue` | Queue controls |
  | `repeat this song` | Loop mode |
  | `what's playing` | Posts the current track to the Discord channel |
  | `post the session code` | Posts the code + dashboard link to Discord |
  | `open the dashboard` | Opens the dashboard in your browser |

  The two posting commands write to the session's own text channel — the same
  place `j!nowplaying` and `j!session` post, using the same embeds, so the
  output is identical however it was triggered. They share a 10-second
  per-guild cooldown, so a misrecognition cannot spam the channel; the second
  of two announces in quick succession reports "Just posted". Asking what's
  playing when nothing is fails on the key rather than announcing that to the
  channel — you're at the Stream Deck, not reading Discord.

  `open the dashboard` opens exactly what the Open Dashboard key opens: both
  go through one shared URL builder, so they cannot drift apart. It is the
  only voice command that acts on your machine rather than on the server —
  the response carries a directive the plugin executes, and the plugin only
  opens `https:` URLs, so a misconfigured or hostile API URL cannot hand it a
  `javascript:` or `file:` target.

  Search terms are taken literally — it will not invent music you didn't
  name; "play something chill" searches for "something chill". Up to 5
  actions run per utterance, in order; if one fails the rest still run and
  the key reports "2 of 3 done".

  **Nothing can be deleted by voice.** The action vocabulary the model is
  constrained to has no verb for removing a playlist, history, or a session,
  and the server re-validates every action against that vocabulary before
  running it — so no phrasing, accidental or adversarial, can produce a
  destructive action. `clear` refers only to the queue. Stop-like speech
  pauses rather than ending the session, for the same reason `stop` was
  excluded before: one misrecognition should not be able to end a session
  with no undo. Use the Stop key for that.

  If OpenAI is unreachable the key falls back to a deterministic parser with
  the same semantics, so an outage degrades to basic single commands instead
  of breaking the key. Recording caps at 15 seconds. The microphone is chosen
  per key in its settings and is open only while the key is held.
- Voice commands appear in the dashboard's Command History with a Voice badge,
  showing both what was heard and the action it ran — one row per action, all
  carrying the same utterance. **Transcripts are stored in Firestore** and
  readable by anyone with the session dashboard; the audio itself is never
  written to disk on either the plugin or the server, and the transcript is
  never written to container logs.
- Voice needs `OPENAI_API_KEY` on the bot. Without it the key reports
  "Voice off" (the route answers 503) — everything else keeps working. The
  same key covers both transcription and interpretation; no second credential.
  `OPENAI_INTENT_MODEL` overrides the interpretation model (default
  `gpt-4o-mini`, roughly $0.0001 and half a second per command).
- Now Playing polls every 5 s, backing off to 30 s while unreachable. It also
  shows the current track's artwork, clearing back to the default icon when
  the track has none or the session ends.
- **Play Playlist** key: configured per key with a server + saved playlist
  (create them with `j!playlist save`). Pressing it inserts that playlist at
  the front of the queue and jumps to it; whatever was already queued stays
  behind it. Needs a live session in that server — ⚠ otherwise.
- **Open Dashboard** key: opens this session's dashboard in your browser. With
  no live session it opens the site's entry page and flashes ⚠. The session
  code is read fresh on every press, so it always points at the current
  session.
- Token revocation: run `j!unlink` in any activated server to revoke all your Stream Deck sign-ins (one command, all devices). Sign in again anytime. Env changes (`make up`) still apply for new deployments.

## Known limitations / follow-ups

- The bundled ffmpeg is pinned by SHA-256 and fetched at pack time
  (`npm run fetch-ffmpeg`), so the `.streamDeckPlugin` is ~30-40 MB and works
  with nothing installed. It is a **Windows** build — the manifest targets
  Windows only. Re-pin deliberately when upgrading; a mismatched hash fails
  the build rather than shipping an unverified binary.


- Summon key icon state (joined/left) comes from press responses only — can go stale if presses fail silently or the session changes mid-flight.
- **Sign-in device-phishing — mitigated, not eliminated.** A relayed authorize link would otherwise hand the sender a token carrying *your* identity. The callback now requires the browser finishing sign-in to come from the same address that started it, so a link sent from elsewhere is rejected ("Sign-in started somewhere else"). The residual gap is an attacker on your own network/NAT; the rule still stands — only complete sign-ins you started from your own Stream Deck.
- Play Playlist key: the playlist dropdown lists what exists when the Property Inspector opens. If you change the server dropdown, re-pick the playlist — the previous server's selection stays stored until you do, and pressing the key would flash ⚠ (404). Same behavior as the Summon key's channel dropdown.
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
