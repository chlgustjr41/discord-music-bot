# MX Creative Console Session Control — Setup & Runbook

Spec: `superpowers/specs/2026-08-14-creative-console-plugin-design.md`
Plugin source: `creative-console-plugin/` · Bot API: `services/bot/src/jacky/api/control.py`

**Jacky Control** is the Logitech MX Creative Console version of the Stream
Deck plugin: the same j! session controls, driven by the same `/control/*`
API, hosted by Logi Options+ (Logi Plugin Service). No bot-side changes —
server setup (Discord OAuth app, Cloudflare tunnel, `COMPOSE_PROFILES=control`)
is identical to the Stream Deck runbook in `docs/streamdeck-control.md`; do it
once and both plugins share it.

## Install

1. Install **Logi Options+** (it brings the Logi Plugin Service).
2. Double-click `JackyControl.lplug4` — Options+ installs it. (Or
   `logiplugintool install JackyControl.lplug4`.)
3. In Options+, open the Creative Console layout and drag any **Jacky Music**
   action onto a key or the dial.

## Sign in

Drop the **Sign In** action on a key and press it:

- The press opens your browser at Discord's consent screen and the key polls
  for completion — up to **5 minutes**, then it gives up (press again to
  retry). A second press while a sign-in is already in flight is ignored.
- The key shows **✓ Signed in** (green) once the token arrives, **Sign in**
  (red) otherwise. The token is stored in plugin settings and persists across
  restarts; every other action starts working the moment sign-in completes.
- **Sign-in must be started from this machine.** The server requires the
  browser that finishes sign-in to come from the same address that started
  it (server-enforced same-address binding). A "sign in to Jacky Music" link
  someone sends you will be rejected ("Sign-in started somewhere else") —
  that link would have handed *them* a token carrying *your* identity. Never
  complete a sign-in link you didn't start yourself. As on the Stream Deck,
  a VPN/proxy that splits plugin and browser onto different addresses can
  block a legitimate sign-in — disable it for the sign-in and retry.
- The Sign In action's editor has an optional **Server URL** textbox for
  non-default domains; leave it empty for
  `https://control.jacky-music-bot.com`. Revoke all sign-ins anytime with
  `j!unlink` in any activated server.

## Actions

All actions live in the **Jacky Music** group. Actions that answer on the
key (Post to Discord, Summon, Play Playlist, Voice) render errors as a
transient label, then return to their normal face. The shared vocabulary:
**Sign in** (401 — not signed in), **Failed** (anything else; the status
code is in the plugin log, never the response body). The plain transport
keys (Skip, Stop, Shuffle, Volume) log failures without key feedback, and
Play/Pause reflects a signed-out state through its live face.

- **Play / Pause** — press toggles playback. The key is a live display,
  repainted from a 5-second poll (backing off to 30 s while the server is
  unreachable): track artwork (YouTube thumbnails fetched at the `mqdefault`
  small variant, size-capped) under a **two-line truncated title** (no
  marquee — ellipsis past two lines), with a `⏸` badge while paused. No
  active session shows a `♪` glyph; not signed in shows "Sign in".
- **Skip / Stop / Shuffle** — simple presses against the same endpoints as
  the `j!` commands. Shuffle keeps the current track playing.
- **Volume** — two forms:
  - **the dial**: an adjustment, each detent sends **diff × 5** (clamped
    0–100 server-side); the dial face shows the current volume from the
    poller, or `—` when there is no session data.
  - **Volume +5 / Volume −5** button actions.
- **Post to Discord** — four draggable actions (Post session code / Post
  now playing / Post queue / Post status) from one parameterised command.
  Posts the same embed as the matching `j!` command, to the session's text
  channel. Hitting the shared 10-second per-guild cooldown shows
  **"Just posted"** (429); a post the server declines shows its detail on
  the key (e.g. **"Queue is empty"**, "Nothing is playing"); success shows
  a brief ✓.
- **Summon** — editor holds **Server** + **Voice channel** listboxes
  (populated live from `/control/channels`; picking a server refreshes the
  channel list). Press toggles: **"Joined `<code>`"** when a session starts
  (the session code, when the server returns one), **"Left"** when the
  press dismissed the bot.
- **Play Playlist** — editor holds **Server** + **Playlist** listboxes
  (from `/control/playlists`, each playlist showing its track count). Press
  inserts the playlist at the front of the queue and jumps to it; the key
  shows **"+N"** for the number of tracks inserted. Needs a live session in
  that server — **"No session"** (409) otherwise.
- **Open Dashboard** — fetches this session's dashboard URL fresh on every
  press and opens it in the default browser. The URL must pass the
  **https-only guard**; a rejected URL opens nothing (logged as
  "dashboard url rejected by guard").
- **Voice Command** — **press-to-toggle** (the console reports presses
  only, so there is no hold-to-talk): press once to start recording — the
  key turns into a red **● REC** face — press again to stop and send.
  Recording auto-stops and sends at **15 seconds**. Editor options:
  - **Microphone** — which capture device to record from. Left unset, the
    key records from **device 0**, the first device it can enumerate — it
    does not ask for a "default" device, because Windows capture has no
    such device and recording against that name captures nothing at all.
  - **Language** — the language you speak; **Auto** by default, with the
    server's seven codes (English, 한국어, 日本語, Español, Français,
    Deutsch, 中文). Naming it beats autodetect on clips this short.
  - **Print debug message to Discord** — off by default; posts what was
    heard and how it resolved to the session's text channel. Turn it back
    off when done: it publishes your transcript to a channel everyone in
    the session can read.

  The recognised vocabulary, grammar-vs-reasoning resolution, and safety
  rules (nothing deletable by voice, "stop" pauses, literal search terms)
  are exactly the Stream Deck key's — see the table in
  `docs/streamdeck-control.md`. What the key says when a press fails:

  | Key shows | Meaning |
  |---|---|
  | `Mic error` | The capture device failed to open or died mid-recording |
  | `No audio` | Nothing was captured, or an empty clip reached the server (400) |
  | `Didn't catch that` | The server heard the clip and could resolve no command (422) |
  | `Just posted` | A posting command hit the 10-second announce cooldown (429) |
  | `Sign in` | Not signed in (401) |

  A response can carry client directives (e.g. "open the dashboard"); the
  plugin executes only `open_url` directives and only for URLs that pass
  the https-only guard. **Voice capture is Windows-only** (NAudio); every
  other action is cross-platform.

## Security notes

- The OAuth token lives in plugin settings storage and is **never logged** —
  logs carry status codes and byte counts only, never bodies, tokens, or
  transcripts.
- Every URL the plugin opens (dashboard, sign-in authorize URL, voice
  `open_url` directives) passes the **https-only guard**, and the string
  that was checked is the string that gets opened — a misconfigured or
  hostile server URL cannot hand the plugin a `javascript:` or `file:`
  target.
- Voice audio is captured **in memory only** (16 kHz mono WAV), posted, and
  discarded — it never touches disk. Transcripts are never logged by the
  plugin (they do appear in the session dashboard's Command History, as
  documented for the Stream Deck).

## Build from source

Requires the .NET 8 SDK and the packaging tool:
`dotnet tool install --global LogiPluginTool`.

If the Logi Plugin Service is not installed on the build machine, populate
the SDK assembly first (it copies PluginApi.dll out of the LogiPluginTool
install into untracked `creative-console-plugin/sdk/`, which the csproj
falls back to):

```powershell
powershell -ExecutionPolicy Bypass -File creative-console-plugin/fetch-pluginapi.ps1
```

Then, from the repo root:

```bash
# Test (68 tests, no device needed)
dotnet test creative-console-plugin/JackyControlPlugin/JackyControlPlugin.sln -p:SkipPluginReload=1

# Package
dotnet build creative-console-plugin/JackyControlPlugin/JackyControlPlugin.sln -c Release -p:SkipPluginReload=1
logiplugintool pack creative-console-plugin/JackyControlPlugin/bin/Release/ creative-console-plugin/JackyControl.lplug4
logiplugintool verify creative-console-plugin/JackyControl.lplug4
```

`-p:SkipPluginReload=1` skips the post-build step that pokes the (possibly
absent) Logi Plugin Service; without the service installed that step only
prints ignorable warnings anyway. The build also drops a `.link` file into
`%LOCALAPPDATA%\Logi\LogiPluginService\Plugins\` for live testing when the
service *is* installed. The packaged `.lplug4` and `sdk/` are untracked —
never commit either.

## On-device checklist

The build machine has no console attached, so key rendering, dial feel, and
editor layout are a manual pass on real hardware:

- [ ] Key images render at rest (glyphs, "Sign in") and while playing
      (artwork + two-line title + `⏸` while paused).
- [ ] The dial adjusts volume in ±5 steps and shows the current level.
- [ ] Action-editor listboxes populate: Server + Voice channel (Summon),
      Server + Playlist (Play Playlist), Microphone + Language (Voice).
- [ ] Sign-in round-trip completes: press → browser consent → key flips to
      ✓ Signed in within the 5-minute window.
- [ ] Voice toggle: press shows ● REC, press again (or 15 s) sends, and the
      command lands in Discord.
- [ ] Post to Discord double-press shows "Just posted".
- [ ] Open Dashboard opens the session dashboard in the default browser.
