# Deployment

The deploy contract on ANY Linux host with Docker:
`git clone` → `cp deploy/.env.example deploy/.env` (fill it) → `make up`.
No cloud-specific dependencies.

## Current host: GCP e2-small
- Project `personal-server-492701`, instance `personal-project-machine`
- SSH: `gcloud compute ssh personal-project-machine --project=personal-server-492701`
- Update deploy: `make deploy` (pulls master, rebuilds, restarts changed services)

## Planned host: Hetzner (~€4/mo, better YouTube IP reputation tier)
Migration = the deploy contract above + repoint the external uptime monitor.

## Secrets
Live only in `deploy/.env` on the host (git-ignored). `.env.example`
documents every variable. Rotate `LAVALINK_PASSWORD` freely: `make up`
re-propagates it to all services.

## External uptime monitor (playbook F7)
The VM cannot report its own death. A free-tier monitor (e.g. UptimeRobot)
pings the guardian's heartbeat endpoint (M4) and emails/DMs on silence.

## M5 production cutover checklist (one-time, operator-run)
The legacy `bot/` stays production until every box is checked:

1. On the VM: fill `deploy/.env` (Discord token, Lavalink password, plugin
   version, webhook, `FIREBASE_SERVICE_ACCOUNT_FILE`, `WEB_APP_URL`).
2. Stop the LEGACY bot container first — the v2 bot must be the only
   gateway session for the token, and the only Firestore writer.
3. `make up` — full v2 stack. `make ps`: lavalink healthy, all services up.
4. `make reauth` — first live run of the v2 device flow (F2 drill).
5. Real-server smoke: `j!start`, `j!play <query>`, dashboard search/queue/
   skip/seek from the web app, `j!stop`.
6. Recovery drills: `make restart s=lavalink` mid-track (expect ≤ seconds
   of gap + auto-resume); `make restart s=bot` mid-track (expect
   convergence resume at position).
7. Confirm a guardian alert arrives in the admin channel (temporarily set
   a wrong `LAVALINK_PASSWORD` in a canary probe window, or watch the
   startup heartbeat).
8. Point the external uptime monitor at the guardian heartbeat.
9. Remove the legacy bot from the VM's startup path; archive `bot/` in a
   follow-up commit once v2 has survived a full week.
