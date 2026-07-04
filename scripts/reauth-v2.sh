#!/bin/sh
# Playbook F2: interactive YouTube OAuth device flow for the v2 stack.
# Blanks the stored refresh token, recreates lavalink (plugin then starts a
# device flow), surfaces the code, waits for approval, captures the new token
# from the plugin's log line, persists it to deploy/.env, and recreates again.
set -eu
COMPOSE="docker compose -f deploy/docker-compose.yml --env-file deploy/.env"

grep -q '^YOUTUBE_OAUTH_REFRESH_TOKEN=' deploy/.env || {
  echo "deploy/.env missing YOUTUBE_OAUTH_REFRESH_TOKEN line" >&2; exit 1; }

sed -i.bak 's/^YOUTUBE_OAUTH_REFRESH_TOKEN=.*/YOUTUBE_OAUTH_REFRESH_TOKEN=/' deploy/.env
$COMPOSE up -d lavalink

echo "==> Waiting for the device code (up to 60s)..."
code_line=""
i=0
while [ $i -lt 30 ]; do
  code_line=$($COMPOSE logs --tail=300 lavalink 2>/dev/null \
    | grep -iE "activate|device" | grep -iE "code" | tail -1 || true)
  [ -n "$code_line" ] && break
  i=$((i + 1)); sleep 2
done
[ -n "$code_line" ] || { echo "no device code appeared in lavalink logs" >&2; exit 1; }
echo "$code_line"
echo "==> Approve in a browser with the bot's Google account. Waiting for the token (up to 5 min)..."

# Google OAuth refresh tokens are prefixed '1//'; YoutubeOauth2Handler logs
# the token at INFO (enabled in application.yml.tmpl).
token=""
i=0
while [ $i -lt 150 ]; do
  token=$($COMPOSE logs --tail=800 lavalink 2>/dev/null \
    | grep -oE '1//[A-Za-z0-9_-]+' | tail -1 || true)
  [ -n "$token" ] && break
  i=$((i + 1)); sleep 2
done
[ -n "$token" ] || { echo "token never appeared — check the YoutubeOauth2Handler log level" >&2; exit 1; }

sed -i.bak "s|^YOUTUBE_OAUTH_REFRESH_TOKEN=.*|YOUTUBE_OAUTH_REFRESH_TOKEN=${token}|" deploy/.env
rm -f deploy/.env.bak
$COMPOSE up -d lavalink
echo "==> New refresh token installed; lavalink recreated. Verify playback or wait for the guardian's next probe."
