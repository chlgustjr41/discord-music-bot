#!/bin/bash
# Deploy / update Jacky Music bot + Lavalink on GCP.
#
# Run locally:   ./deploy/gcp-deploy.sh
# Requires:      gcloud CLI authenticated with access to the personal-server project.
#
# This script:
#   1. Ensures the VM exists (creates it on first run).
#   2. Copies the repo contents (bot/, lavalink/, docker-compose.yml) to the VM.
#   3. Uploads the local .env to the VM.
#   4. Runs `docker compose up -d --build` on the VM.

set -euo pipefail

PROJECT_ID="personal-server-492701"
INSTANCE_NAME="personal-project-machine"
ZONE="${GCP_ZONE:-us-east1-b}"
REMOTE_DIR="/opt/jacky-music"
ENV_FILE="${ENV_FILE:-.env}"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE not found. Create it from .env.example first."
  exit 1
fi

echo "==> Checking for instance $INSTANCE_NAME in $PROJECT_ID..."
if ! gcloud compute instances describe "$INSTANCE_NAME" \
      --project="$PROJECT_ID" --zone="$ZONE" >/dev/null 2>&1; then
  echo "==> Instance not found — creating e2-small VM."
  gcloud compute instances create "$INSTANCE_NAME" \
    --project="$PROJECT_ID" \
    --zone="$ZONE" \
    --machine-type=e2-small \
    --image-family=ubuntu-2204-lts \
    --image-project=ubuntu-os-cloud \
    --boot-disk-size=20GB \
    --tags=jacky-bot \
    --metadata-from-file=startup-script=deploy/startup.sh
  echo "==> Waiting 60s for startup-script to finish installing Docker..."
  sleep 60
fi

echo "==> Ensuring remote dir $REMOTE_DIR exists..."
gcloud compute ssh "$INSTANCE_NAME" \
  --project="$PROJECT_ID" --zone="$ZONE" \
  --command="sudo mkdir -p $REMOTE_DIR && sudo chown \$USER $REMOTE_DIR"

echo "==> Uploading project files..."
gcloud compute scp --recurse \
  --project="$PROJECT_ID" --zone="$ZONE" \
  bot lavalink docker-compose.yml \
  "$INSTANCE_NAME:$REMOTE_DIR/"

echo "==> Uploading .env..."
gcloud compute scp \
  --project="$PROJECT_ID" --zone="$ZONE" \
  "$ENV_FILE" "$INSTANCE_NAME:$REMOTE_DIR/.env"

echo "==> Building & starting containers..."
gcloud compute ssh "$INSTANCE_NAME" \
  --project="$PROJECT_ID" --zone="$ZONE" \
  --command="cd $REMOTE_DIR && sudo docker compose up -d --build"

echo "==> Done. Tail logs with:"
echo "    gcloud compute ssh $INSTANCE_NAME --project=$PROJECT_ID --zone=$ZONE --command='sudo docker compose -f $REMOTE_DIR/docker-compose.yml logs -f jacky-bot'"
