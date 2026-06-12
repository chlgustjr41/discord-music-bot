#!/usr/bin/env bash
# One-command recovery for a dead YouTube OAuth refresh token.
#
# Symptom this fixes: every track fails to play; Lavalink logs show
#   "Client [...] failed: This video requires login"  and/or
#   "Invalid status code for oauth2 token fetch: 400" spam.
# Cause: Google revoked the OAuth refresh token (it does this periodically).
#
# What it does:
#   1. Blanks YOUTUBE_OAUTH_REFRESH_TOKEN in .env
#   2. Recreates the lavalink container (fresh env + fresh, readable log)
#   3. Prints the google.com/device code — go authorize it in a browser
#   4. Waits for the new refresh token, writes it to .env
#   5. Recreates the container again so the token persists across restarts
#   6. Verifies track loading via the Lavalink REST API
#
# Usage (on the VM, from the repo root):
#   bash scripts/reauth-youtube.sh
set -euo pipefail

cd "$(dirname "$0")/.."

ENV_FILE=.env
LAVALINK_PASSWORD=$(grep -oP '(?<=LAVALINK_PASSWORD=).*' "$ENV_FILE" 2>/dev/null || echo youshallnotpass)
DOCKER="docker"
docker info >/dev/null 2>&1 || DOCKER="sudo docker"

echo "==> Backing up $ENV_FILE and blanking the old token"
sudo cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%Y%m%d%H%M%S)"
sudo sed -i 's|^YOUTUBE_OAUTH_REFRESH_TOKEN=.*|YOUTUBE_OAUTH_REFRESH_TOKEN=|' "$ENV_FILE"

echo "==> Recreating lavalink container (this also resets its log)"
$DOCKER compose up -d --force-recreate lavalink

echo "==> Waiting for the device code (up to 2 min)..."
CODE_LINE=""
for _ in $(seq 1 24); do
  CODE_LINE=$($DOCKER logs jacky-lavalink 2>&1 | grep -m1 'OAUTH INTEGRATION: To give' || true)
  [ -n "$CODE_LINE" ] && break
  sleep 5
done
if [ -z "$CODE_LINE" ]; then
  echo "ERROR: no device code appeared. Check: $DOCKER logs jacky-lavalink" >&2
  exit 1
fi

CODE=$(echo "$CODE_LINE" | grep -oP 'enter code \K[A-Z-]+')
echo
echo "############################################################"
echo "##  Go to https://www.google.com/device                  ##"
echo "##  Enter code: $CODE"
echo "##  (Use a burner Google account, not your main one.)    ##"
echo "############################################################"
echo

echo "==> Waiting for you to authorize (up to 15 min)..."
TOKEN_LINE=""
for _ in $(seq 1 90); do
  TOKEN_LINE=$($DOCKER logs jacky-lavalink 2>&1 | grep -m1 'Token retrieved successfully' || true)
  [ -n "$TOKEN_LINE" ] && break
  sleep 10
done
if [ -z "$TOKEN_LINE" ]; then
  echo "ERROR: token never arrived — the code probably expired. Re-run this script." >&2
  exit 1
fi

TOKEN=$(echo "$TOKEN_LINE" | grep -oP '\(\K[^)]+')
echo "==> Token received. Writing it to $ENV_FILE"
sudo sed -i "s|^YOUTUBE_OAUTH_REFRESH_TOKEN=.*|YOUTUBE_OAUTH_REFRESH_TOKEN=$TOKEN|" "$ENV_FILE"

echo "==> Recreating lavalink so the token persists"
$DOCKER compose up -d --force-recreate lavalink

echo "==> Waiting for Lavalink to come back up..."
sleep 30

echo "==> Verifying track loading"
RESULT=$(curl -s -H "Authorization: $LAVALINK_PASSWORD" \
  "http://localhost:2333/v4/loadtracks?identifier=ytsearch:never%20gonna%20give%20you%20up" || true)
if echo "$RESULT" | grep -q '"loadType":"search"'; then
  echo "SUCCESS: YouTube track loading works."
else
  echo "WARNING: verification failed. Response was:" >&2
  echo "$RESULT" | head -c 500 >&2
  echo >&2
  echo "Playback may still need OAuth warm-up; test a direct video URL too:" >&2
  echo "  curl -H \"Authorization: $LAVALINK_PASSWORD\" 'http://localhost:2333/v4/loadtracks?identifier=https://www.youtube.com/watch?v=dQw4w9WgXcQ'" >&2
  exit 1
fi
