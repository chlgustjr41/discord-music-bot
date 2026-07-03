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
