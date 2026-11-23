# System Status

> Snapshot of the running system. Update when production state changes
> (cutover events, incidents, host moves) — not for routine deploys.

**Last updated: 2026-11-22**

## Production

- **Host:** GCP e2-medium `personal-project-machine` (us-east1-b,
  project `personal-server-492701`). **A move to a home NAS (UGREEN
  DXP2800) is prepared but not yet done** — procedure in
  [operations/NAS-SETUP.md](operations/NAS-SETUP.md). The VM also hosts an
  unrelated project, so retire it deliberately.
- **Stack:** v2 (all five compose services) — **cut over 2026-07-04**,
  replacing the legacy `bot/`. The bot converged mid-session at cutover and
  resumed the playing track at position.
- **Legacy v1:** no longer a rollback path. Its compose file, deploy
  scripts and the `rollback-legacy` target are removed from the repository
  (they published the audio server on the host with a default password).
  The stopped legacy containers still on the VM go away with the VM.
- **Alerting:** guardian → Discord webhook (live, heartbeat verified
  2026-07-04). Weekly heartbeat proves the channel (F9); alerts carry
  playbook IDs keyed to the [runbook](operations/RUNBOOK.md).
- **Secrets:** `deploy/.env` + `deploy/firebase-service-account.json` on
  the VM only (mode 600, git-ignored).

## Known issues / degradations

- **F3 (active, degraded-but-serving):** YouTube changed its player JS
  (`/s/player/4918c89a/`); youtube-source **1.18.1 — the latest release —
  cannot extract the signature function**
  (`ScriptExtractionException: Must find sig function from script`).
  Effect: normal playback works (TV client needs no cipher); **seek and
  position-resume fail**, so convergence after a restart skips to the next
  track instead of resuming, and occasional mid-queue stalls trigger the
  guardian's F6 bot-restart (observed MTTR ≈ 4 min, self-healing).
  Fix: when upstream releases (daily watcher alerts on it), bump
  `YOUTUBE_PLUGIN_VERSION` in `deploy/.env` and `make up`.
- **Legacy STT bot:** removed 2026-07-04 (container, image, env). It was
  not part of the rewrite and had been crash-looping. Code survives on
  branch `backup-stt-era-20260612` if ever wanted.

## Operator checklist (remaining)

- [ ] External uptime monitor pointed at the VM (playbook F7) — the
      guardian cannot report its own host's death.
- [ ] `make reauth` drill once, to validate the F2 flow live (the carried-
      over OAuth token currently works; drill before you need it).
- [ ] Cut over to the NAS (NAS-SETUP.md), then retire the VM.
- [ ] Archive `bot/` (legacy source, reference only).

## Health signals (what "working" looks like)

- `make ps` — lavalink + pot-provider `(healthy)`, all five `Up`.
- Guardian log quiet between probes; alerts channel silent except the
  weekly 💓.
- `make logs s=token-minter` shows "minted and pushed" every ~5.5h.
- `j!status` and the bot's `/health` report the audio server's frame
  statistics. (Releases from the NAS hardening on no longer write an HTTP
  access log, so `GET /health` lines are gone from the bot log by design.)
