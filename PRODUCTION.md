# Production deployment

This page used to describe the original (v1) deployment on a GCP VM. That
stack is retired; its compose file and deploy scripts have been removed
because they published the audio server on the host network with a default
password, which is not acceptable on any network that has other devices on it.

## The bot stack (Docker Compose)

- How to deploy on any Docker host, secrets, and what may talk to what:
  [docs/operations/DEPLOYMENT.md](docs/operations/DEPLOYMENT.md)
- Step-by-step installation on the home NAS, including the host firewall rule:
  [docs/operations/NAS-SETUP.md](docs/operations/NAS-SETUP.md)
- What each service is for:
  [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md)
- What to do when something breaks:
  [docs/operations/RUNBOOK.md](docs/operations/RUNBOOK.md)

## The web dashboard (Firebase)

The dashboard is a static React app on Firebase Hosting. It reads and writes
Firestore directly, under the rules in `firestore.rules`. The Firestore
database is a **named** database, `discord-music-bot`, not `(default)`.

```bash
cd frontend
npm ci
npm run build
cd ..
npx firebase deploy --only hosting            # the app
npx firebase deploy --only firestore:rules    # the access rules
```

Deploy the rules and the app together when a change touches both: the app
must only attempt what the rules allow. Rule tests live in `firestore-tests/`
and run against the local emulator.

### Frontend environment (`frontend/.env`)

| Variable | Value |
|----------|-------|
| `VITE_FIREBASE_API_KEY` | Firebase web API key (public by design) |
| `VITE_FIREBASE_AUTH_DOMAIN` | `discord-bot-jacky-music.firebaseapp.com` |
| `VITE_FIREBASE_PROJECT_ID` | `discord-bot-jacky-music` |
| `VITE_FIREBASE_STORAGE_BUCKET` | Firebase storage bucket |
| `VITE_FIREBASE_MESSAGING_SENDER_ID` | Firebase sender ID |
| `VITE_FIREBASE_APP_ID` | Firebase app ID |
