# Deployment

The deploy contract on ANY Linux host with Docker:
`git clone` → `cp deploy/.env.example deploy/.env` (fill it) → `make up`.
No cloud-specific dependencies, and no inbound network access: every
connection the stack makes is outbound.

## Target host: home NAS (UGREEN DXP2800)

Step-by-step installation, including the host firewall rule that keeps the
containers off the home network: **[NAS-SETUP.md](NAS-SETUP.md)**.

- Update: `git pull && make up` (or the raw compose command in NAS-SETUP.md
  when `make` is not installed), then re-run its verification section.
- A host on a home network needs one thing a cloud VM did not: containers must
  not be able to reach private addresses. `deploy/nas/egress-firewall.sh`
  enforces that, and the compose file pins the subnets it keys on
  (172.28.0.0/16) and gives every service public DNS resolvers so name
  resolution keeps working behind it.

## Previous host: GCP `personal-project-machine`

Ran the stack from 2026-04 until the NAS cutover. It also hosts an unrelated
project (`geo-assistant`), so retire it deliberately, not as a side effect.
SSH: `gcloud compute ssh personal-project-machine --project=personal-server-492701`.

## Secrets

Live only in `deploy/.env` and `deploy/firebase-service-account.json` on the
host. Every env file except the `.env.example` templates is git-ignored.
`.env.example` documents every variable.

- Rotate `LAVALINK_PASSWORD` and `YOUTUBE_REMOTE_CIPHER_PASSWORD` freely:
  `make up` re-propagates them.
- The bot, guardian and token-minter run as uid 10001. The service-account
  file is bind-mounted read-only and must be readable by that uid
  (`chown 10001:10001`, mode 400).
- The service account needs the `Cloud Datastore User` role and nothing else;
  the bot only reads and writes Firestore.
- `DOCKER_GID` must equal the group of `/var/run/docker.sock` on the host, or
  the socket proxy cannot reach Docker and guardian loses its restart ability
  (it keeps alerting).

## What may talk to what

No service publishes a host port. Inside the stack, five networks limit reach:

| Network | Members | Why |
|---------|---------|-----|
| `core` | bot, lavalink, guardian, token-minter | The working set |
| `cipher` | lavalink, cipher | Only Lavalink may call the cipher sidecar, which executes JavaScript served by YouTube |
| `pot` | token-minter, pot-provider | Only the minter may ask for tokens |
| `edge` | bot, cloudflared | The tunnel reaches the bot and nothing else |
| `dockerproxy` (no internet) | guardian, socket-proxy | Only guardian may reach the Docker API proxy |

## External uptime monitor (playbook F7)

The host cannot report its own death, and on a home connection that includes
power cuts and ISP outages. Use a push-style heartbeat monitor that alerts
when pings stop. Do not publish guardian's `/status` port to satisfy a
pull-style monitor: it is unauthenticated and is meant for the bot only.

## Moving hosts

Only one bot may be connected with the bot token at a time, and both hosts
would write the same Firestore documents. A move is therefore: stop the old
stack, reset the bot token (which also guarantees the old host cannot come
back by accident), start the new stack. Session state is in Firestore, so
queues survive. NAS-SETUP.md section 6 is the procedure.
