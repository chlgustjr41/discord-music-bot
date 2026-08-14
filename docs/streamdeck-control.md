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
  before that is lost.

  **Structure decides first.** The vocabulary below is recognised *directly*
  by a deterministic grammar — no model involved, so those commands are
  instant, free, and give the same answer every time. Anything the grammar
  does not recognise goes on to a reasoning layer, which maps natural
  phrasings ("turn this thing down a bit", "skip this, then add two songs by
  Radiohead") onto exactly the same closed set of commands. It cannot invent
  a command that is not in that set.

  | Say | Result |
  |---|---|
  | `play <song>` | **Interrupts** and plays it now |
  | `play <song> next` | Front of the queue, without interrupting |
  | `add <song>` / `queue <song>` | Appends to the end |
  | `play playlist <name>`, `add playlist <name>`, `<…> next` | Same three placements, for a **saved** playlist |
  | `skip`, `next`, `next song`, `skip two` | Skip one or several |
  | `pause`, `resume`, `continue`, `stop` | Stop-like speech **pauses** |
  | `louder` / `quieter`, `volume up` / `down`, `volume 40` | Relative or absolute |
  | `shuffle`, `clear`, `clear the queue` | Queue controls |
  | `repeat`, `loop track`, `loop queue`, `loop off` | Loop mode |
  | `what's playing`, `now playing` | Posts the current track to the Discord channel |
  | `session code`, `post the session code` | Posts the code + dashboard link to Discord |
  | `queue`, `show the queue`, `what's in the queue` | Posts the queue listing to Discord |
  | `status`, `health`, `bot status` | Posts the full health embed to Discord |
  | `open the dashboard` | Opens the dashboard in your browser |

  Three rules are worth knowing because they are what the key's behaviour
  turns on:

  - **A phrase it cannot place does nothing.** It is not searched. The key
    says "Didn't catch that" and nothing is dispatched. This is a change: a
    misheard phrase used to become a YouTube search that also *replaced what
    was playing*, so the cost of a mumble was losing your track. Silence is
    the safer default, and it is the behaviour you will notice first.
  - **The current track is only replaced when you say "play"** (or ask for a
    skip). Anything else that resolves to adding music goes to the end of the
    queue instead of interrupting — enforced server-side, after the model,
    not merely requested of it.
  - **"playlist" always means a saved session playlist**, never a YouTube
    search. Say `play playlist chill` and you get the playlist you saved with
    `j!playlist save`, never a search for the words "playlist chill".

  Note that "next" means two different things and position settles it:
  *leading* "next" is a skip (`next song` skips), *trailing* "next" is
  placement (`play X next` queues it first).

  The four posting commands go through the same announcer the **Post to
  Discord key** uses — same embeds as `j!session` / `j!nowplaying` /
  `j!queue` / `j!status`, same destination (the session's text channel, else
  the text chat of the voice channel the bot is standing in). One 10-second
  per-guild cooldown is **shared between voice and the key**: two features
  posting into one channel get one spam bound, so a voice announce followed
  quickly by a key press reports "Just posted", and vice versa. Asking
  what's playing when nothing is (or for an empty queue) fails on the key
  rather than announcing that to the channel — you're at the Stream Deck,
  not reading Discord.

  `open the dashboard` opens exactly what the Open Dashboard key opens: both
  go through one shared URL builder, so they cannot drift apart. It is the
  only voice command that acts on your machine rather than on the server —
  the response carries a directive the plugin executes, and the plugin only
  opens `https:` URLs, so a misconfigured or hostile API URL cannot hand it a
  `javascript:` or `file:` target.

  Search terms are taken literally — it will not invent music you didn't
  name; "play something chill" searches for "something chill". A bare noun
  phrase with no verb is *not* a search: say "play" if you want something
  played. Up to 5 actions run per utterance, in order; if one fails the rest
  still run and the key reports "2 of 3 done".

  **Nothing can be deleted by voice.** The action vocabulary the model is
  constrained to has no verb for removing a playlist, history, or a session,
  and the server re-validates every action against that vocabulary before
  running it — so no phrasing, accidental or adversarial, can produce a
  destructive action. `clear` refers only to the queue. Stop-like speech
  pauses rather than ending the session, for the same reason `stop` was
  excluded before: one misrecognition should not be able to end a session
  with no undo. Use the Stop key for that.

  The grammar *is* the fallback now, so if the reasoning layer is unreachable
  every command in the table above still works exactly as it always does — an
  outage costs you the flexible phrasings, not the key. (Transcription still
  needs OpenAI; without it the key cannot hear you at all.)

  Three per-key settings in the Property Inspector:

  - **Microphone** — which input device to record from. Open only while the
    key is held. Recording caps at 15 seconds. Left unset, the key records
    from the **first device it can enumerate** and says which one in the log;
    it does not fall back to a "default" device, because Windows has no such
    DirectShow device and recording against that name captured nothing at all.
    With no audio inputs on the machine the key says "No mic" rather than
    spawning a capture that cannot work.
  - **Language** — the language you speak, **English by default**. Naming it
    beats autodetect by a wide margin on clips this short, so set it if you
    give commands in Korean, Japanese, Spanish, French, German, or Chinese.
    It is per key, so one deck can carry an English key and a Korean one. An
    unrecognised code degrades to English rather than breaking the key.
  - **Print debug message to Discord** — **off by default**, and per key.
    With it on, every press posts one message to the session's own text
    channel saying what was heard, **how** it was resolved (the grammar, the
    reasoning layer, or nothing at all), and each action with its result:

    ```
    🎙️ Heard: "play playlist chill next"
    Resolved by: grammar
    Actions: playlist(chill, next) → Queued 12
    ```

    This is the thing to turn on when a command does not do what you
    expected — the middle line is what answers "why did it do *that*", and it
    posts even when nothing was recognised, which is the most useful case.
    It is not subject to the 10-second announce cooldown, so it never
    silently drops. Turn it back off when you are done: it publishes your
    transcript to a channel **everyone in the session can read**, which is
    exactly why it is opt-in and why no key arrives with it on.

  What the key says when a press does not work, and where each one points:

  | Key shows | Meaning |
  |---|---|
  | `No mic` | The machine reports no audio input devices at all |
  | `No ffmpeg` | The capture binary is in neither the bundle nor PATH |
  | `Mic error` | ffmpeg opened and died — exit code and its stderr are in the plugin log |
  | `Hold longer` | The capture ran but the press was too short to produce audio |
  | `No audio` | An empty clip reached the server (400); the fault is this end of the wire |
  | `Didn't catch that` | The server heard something and could resolve no command from it (422) |

  The last two used to be one message, which is why a microphone that never
  opened read as speech nobody understood.
- Voice commands appear in the dashboard's Command History with a Voice badge,
  showing both what was heard and the action it ran — one row per action, all
  carrying the same utterance. **Transcripts are stored in Firestore** and
  readable by anyone with the session dashboard; the audio itself is never
  written to disk on either the plugin or the server, and the transcript is
  never written to container logs.
- Voice needs `OPENAI_API_KEY` on the bot. Without it the key reports
  "Voice off" (the route answers 503) — everything else keeps working. The
  same key covers both transcription and interpretation; no second credential.
  `OPENAI_INTENT_MODEL` overrides the reasoning model (default `gpt-4o-mini`,
  roughly $0.0001 and half a second) — charged only for utterances the
  grammar could not resolve, which is why the table above costs nothing.
- **Play/Pause** key: presses toggle playback, and two per-key options in the
  Property Inspector turn it into the display as well — this is the old Now
  Playing key merged in, so one key does both jobs.
  - **Show track title** marquees the current track (400 ms scroll clock, not
    the 5 s poll, so it actually reads as scrolling) with a `⏸` suffix while
    paused.
  - **Show artwork** replaces the play/pause glyph with the track thumbnail,
    letterboxed — the original aspect ratio is preserved and the whole image
    fits inside the key, never stretched or cropped. It falls back to the
    glyph when the track has no artwork, when the session ends, or when the
    option is switched back off, so a stale cover never outlives its track.
    YouTube thumbnails are fetched at their **small** variant (`mqdefault`,
    320×180 — still four times a 72-pixel key's resolution). The full-size
    `maxresdefault` encodes to roughly 258,000 characters, and a payload that
    large simply does not render; anything still over 64 KB encoded is
    skipped, with its size logged, and the glyph is kept.

  Both default **off**, so a Play/Pause key you never reconfigured behaves
  exactly as before. Settings are per key: two Play/Pause keys can be
  configured differently. Polling is every 5 s, backing off to 30 s while
  unreachable.

  > **The separate Now Playing key is gone.** If you had one on your deck it
  > will show as an unknown key after this update — drop a Play/Pause key in
  > its place and switch both options on to get the same display, plus
  > press-to-toggle.
- **Post to Discord** key: configured per key with what to post — Session
  code, Now playing, Queue, or Status. Pressing it posts **the same embed the
  matching `j!` command posts** (`j!session`, `j!nowplaying`, `j!queue`,
  `j!status`): both paths call the same embed builders, so they cannot drift.
  The destination is the session's text channel when one exists (the channel
  a `j!` command was typed in), otherwise **the text chat of the voice
  channel the bot is standing in** — so a session started from the deck or
  the web, which never had an invoking text channel, posts where the session
  actually lives instead of failing with "Could not post". Posts share a 10-second per-guild cooldown —
  a mashed key shows "Just posted" instead of spamming the channel. Now
  playing with nothing playing and Queue with an empty queue fail on the key
  and post nothing (you're at the deck, not reading Discord); Status always
  posts. Needs a live session — ⚠ otherwise. Posts appear in the dashboard's
  Command History under the `j!` name. Two keys can be configured differently.
- **Shuffle** key: shuffles the current queue in place; the track that is
  playing keeps playing. Needs a live session — ⚠ otherwise. An empty queue
  is not an error. Shuffles appear in the dashboard's Command History.
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
- The Play/Pause key's `⏸` glyph (shown with **Show track title** on) may
  render as a missing-glyph box on some Stream Deck firmware fonts — swap for
  `||` in `streamdeck-plugin/src/actions/play-pause.ts` if it looks wrong.
- The marquee advances 2 characters per poll tick (every 5 s) — slow scroll
  is by design (poll-driven), not a bug.
- Plugin tests are not wired into `make test` (services/ only); run them with
  `cd streamdeck-plugin && npm test`.
