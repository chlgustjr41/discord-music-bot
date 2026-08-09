# Operator Runbook

Guardian alerts carry a playbook ID (F1–F9). Find the ID below; run exactly
what it says. All commands run on the VM from the repo root.

> **Status:** v2 is PRODUCTION (cut over 2026-07-04). Alerts carry playbook
> IDs; automated responses run as described per ID. Live system state and
> known degradations: [../STATUS.md](../STATUS.md).

> **Reading logs:** `make logs s=<svc>` follows live output (Ctrl-C to exit;
> silence after a few seconds means no match) and starts from the last 100
> lines. To search further back without following:
> `docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs --tail=1000 <svc> | grep -i <pattern>`

## Playbook index

| ID | Failure | Detected by | Automated response | Human needed? |
|----|---------|-------------|--------------------|---------------|
| F1 | poToken stale/rejected | Canary: bot-detection error | Trigger token-minter immediately, re-probe | No |
| F2 | OAuth token revoked | Canary: "requires login" + OAuth 400 | Alert with one-command re-auth (`make reauth`) | **Yes** (~60s device flow) |
| F3 | Plugin broken by YouTube JS change | Canary: signature/cipher errors | Alert with exact version bump; release watcher usually warns first | Yes (approve bump) |
| F4 | Lavalink sick/dead | Canary timeout / Docker health | Guardian restarts container; bot reconnects + restores from Firestore | No |
| F5 | Bot hung (gateway zombie) | Guardian's bot health ping | Guardian restarts bot container | No |
| F6 | Silent playback (position frozen) | Position comparison across probes | Restart playback via bot; escalate to container restart on repeat | No |
| F7 | VM down / Docker dead | External uptime monitor (free tier) pinging guardian's heartbeat URL | External email/DM | Yes |
| F8 | Firestore unreachable | Bot + guardian error rates | Continue from in-memory cache; queue writes, flush on recovery; alert if sustained | No |
| F9 | Alert channel broken | Weekly guardian heartbeat message | — | Missing heartbeat noticed by operator |

## F1 — poToken stale or rejected

**Alert looks like:** `[F1] poToken rejected — canary load hit YouTube bot
detection. Triggered token-minter; re-probing in 2 min.`

**Confirm:** `make logs s=lavalink | grep -i "sign in to confirm"` shows
bot-detection errors on track loads.

**Fix:** normally none — the guardian (M4) triggers an immediate token-minter
run and re-probes. If the alert repeats:
1. `make restart s=token-minter` — the minter mints immediately at startup and pushes to Lavalink at runtime (no Lavalink restart needed)
2. `make restart s=lavalink` — only if the push failed; reloads tokens from the shared volume at cold start
3. Verify: play a test track, or `make logs s=guardian` shows the canary passing

**While pending:** the OAuth layer (F2) and client ordering keep most
playback working; only bot-detection-gated loads fail.

## F2 — YouTube OAuth token revoked

> `make reauth` drives the v2 stack: it blanks the stored token, restarts
> Lavalink into a device flow, prints the code, auto-captures the new refresh
> token from the plugin log, writes it to `deploy/.env`, and recreates the
> container. Manual fallback if the log-scrape fails: copy the token into
> `deploy/.env` yourself, then `make up` (a plain restart does NOT re-read
> env changes).

**Alert looks like:** `[F2] OAuth revoked — all loads failing with "requires
login". Run: make reauth`

**Confirm:** `make logs s=lavalink | grep -i oauth` shows
`Invalid status code for oauth2 token fetch: 400` repeating.

**Fix (~60s):**
1. `make reauth` — prints a device code and URL (google.com/device)
2. Open the URL on any machine, enter the code, approve with the bot's Google account
3. The script captures the new token automatically and recreates Lavalink
4. Verify: guardian posts `[F2 resolved]` after its next probe (≤2 min)

**While pending:** poToken layer (F1) keeps most tracks playing; only
sign-in-gated tracks fail.

## F3 — youtube-source plugin broken by a YouTube JS change

**Alert looks like:** `[F3] Signature extraction failing — youtube-source
vX.Y.Z is stale. Bump YOUTUBE_PLUGIN_VERSION in deploy/.env and restart
lavalink.` (The daily release watcher usually warns before breakage.)

**Confirm:** `make logs s=lavalink | grep -iE "cipher|signature"` shows
extraction/decipher errors on every load.

**Fix (~2 min):**
1. Find the latest release at github.com/lavalink-devs/youtube-source/releases
2. Edit `YOUTUBE_PLUGIN_VERSION` in `deploy/.env` (the only place the version lives)
3. `make restart s=lavalink` — the entrypoint re-renders `application.yml` from the template, so config and jar cannot drift
4. Verify: play a test track; canary passes on the next probe

**While pending:** all YouTube loads fail; no auth layer helps against a
signature break. This is the highest-urgency human playbook.

## F4 — Lavalink sick or dead

**Alert looks like:** `[F4] Lavalink unresponsive (canary timeout) —
restarting container.`

**Confirm:** `make ps` shows `lavalink` restarting or exited;
`make logs s=lavalink` shows a crash, OOM kill, or startup failure.

**Fix:** automated — the guardian restarts the container; the bot's client
reconnects with backoff and re-issues "play at position X" from Firestore
(seconds of gap). Manually:
1. `make restart s=lavalink`
2. Verify: `make ps` shows healthy; playback resumes

**If it crash-loops:** check memory pressure (`docker stats`) — Lavalink's
budget is ~800MB RSS on the 2GB VM — and inspect `make logs s=lavalink`
for a config error before restarting again.

### F4a — boot-loops on the plugin download

**Looks like:** the log never gets past
`Downloading …youtube-plugin-….jar`, and restarts every ~3 min.
`docker inspect` shows `ExitCode=0`, `OOMKilled=false` — not a crash.

**Confirm it's upstream, not us:** from the VM, `curl` GitHub (should be
200) and `https://maven.lavalink.dev/` (times out). Then curl maven **from
your dev machine too** — if it fails there as well, the Maven repo is down
globally and nothing on the VM will fix it. This happened on 2026-08-09.

**Fix:** switch to local-jar mode, which sources the jar from GitHub
releases instead. In `deploy/.env`, set `YOUTUBE_PLUGIN_VERSION` to a
**release tag** (GitHub has no snapshot builds) and set
`YOUTUBE_PLUGIN_SHA256` to that jar's checksum, then `make up`. The
entrypoint downloads, verifies, and fails closed on mismatch.

**If it then fails with `curl: (23) Failure writing output to
destination`:** the plugins volume is root-owned and Lavalink runs as uid
322. Fix the existing volume with
`docker run --rm -v jacky-music_lavalink_plugins:/p alpine chown -R 322:322 /p`.
(The Dockerfile now creates the dir owned correctly, so fresh volumes are
fine — this only bites volumes created before that change.)

**Why it can happen at all:** `docker compose up -d <service>` also brings
up that service's dependencies and recreates them when their config hash
changed. So deploying only the bot can recreate Lavalink. Since 2026-08-09
`/opt/Lavalink/plugins` is a named volume, so a recreate no longer needs to
re-download — but a first-ever boot on a new VM still does.

## F5 — Bot hung (gateway zombie)

**Alert looks like:** `[F5] Bot health ping timed out — restarting bot
container.`

**Confirm:** `make logs s=bot` shows no recent gateway activity or repeating
heartbeat/resume failures; bot appears online in Discord but ignores commands.

**Fix:** automated — the guardian restarts the bot container. Manually:
1. `make restart s=bot`
2. The bot is stateless: it rebuilds player state from Firestore and re-attaches to Lavalink's session (audio continues during the gap)
3. Verify: bot responds to a command; guardian's next bot ping passes

## F6 — Silent playback (position frozen)

**Alert looks like:** `[F6] Player position frozen across probes while state
says playing — restarting playback.`

**Confirm:** Discord shows "now playing" but no audio; `make logs s=guardian`
shows the position comparison failing between probes.

**Fix:** automated — the guardian restarts playback via the bot, escalating
to a container restart on repeat. Manually:
1. Skip or replay the current track from Discord or the dashboard
2. If still silent: `make restart s=lavalink`, then replay — the bot re-issues "play at position X" from Firestore
3. Verify: position advances between the next two guardian probes

## F7 — VM down / Docker dead

**Alert looks like:** an email/DM from the **external uptime monitor** (not
the guardian — the VM cannot report its own death): "guardian heartbeat URL
unreachable".

**Confirm:**
`gcloud compute ssh personal-project-machine --project=personal-server-492701`
— if SSH fails, check the instance in the GCP console.

**Fix:**
1. Start (or reset) the instance from the GCP console if it is stopped/wedged
2. SSH in; if Docker died but the VM is up: `sudo systemctl restart docker`
3. From the repo root: `make up` — every service is crash-only, so a cold start converges on its own
4. Verify: `make ps` all healthy; uptime monitor reports the heartbeat URL reachable

## F8 — Firestore unreachable

**Alert looks like:** `[F8] Firestore error rate sustained for >5 min —
serving from cache, queueing writes.`

**Confirm:** `make logs s=bot | grep -i firestore` shows
UNAVAILABLE/deadline-exceeded errors; check status.cloud.google.com for a
Firestore incident.

**Fix:** normally none — the bot continues from its in-memory cache and
flushes queued writes when Firestore recovers (lands with the M3 bot). If
sustained beyond a GCP incident window:
1. `make restart s=bot` after connectivity returns to force a clean state rebuild
2. Verify: writes flush and the alert clears on the next probe

**Impact while pending:** playback continues; dashboard sync and queue
persistence lag until writes flush.

## F9 — Alert channel broken

**Alert looks like:** nothing — that is the failure. The guardian posts a
weekly heartbeat message; its **absence** is the signal (calendar reminder
recommended).

**Confirm:** `make logs s=guardian | grep -i webhook` shows delivery errors
(HTTP 4xx — webhook deleted or channel removed).

**Fix:**
1. Re-create the webhook in the Discord admin channel (Server Settings → Integrations → Webhooks)
2. Update `ALERT_WEBHOOK_URL` in `deploy/.env`
3. `make restart s=guardian`
4. Verify: guardian posts its startup/heartbeat message to the channel
