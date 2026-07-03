#!/bin/sh
set -eu
: "${YOUTUBE_PLUGIN_VERSION:?YOUTUBE_PLUGIN_VERSION must be set (see deploy/.env.example)}"
case "$YOUTUBE_PLUGIN_VERSION" in
  *[!A-Za-z0-9._-]*) echo "YOUTUBE_PLUGIN_VERSION contains invalid characters" >&2; exit 1 ;;
esac
sed "s|__YOUTUBE_PLUGIN_VERSION__|${YOUTUBE_PLUGIN_VERSION}|g" \
    /opt/Lavalink/application.yml.tmpl > /tmp/application.yml
# Cold-start poToken injection: the token-minter persists tokens.env to the
# shared volume; values are charset-guarded at write time (ADR-0004: websafe
# base64 / URL-encoded, may contain '%'), so the '|'-delimited sed is safe.
TOKENS_FILE="${TOKENS_FILE:-/data/tokens/tokens.env}"
if [ -f "$TOKENS_FILE" ]; then
  . "$TOKENS_FILE"
  sed -i "s|__POT_TOKEN__|${POT_TOKEN}|; s|__POT_VISITOR_DATA__|${POT_VISITOR_DATA}|" /tmp/application.yml
else
  # No minted tokens yet (first boot): drop the pot block entirely.
  sed -i '/# POT_BLOCK_START/,/# POT_BLOCK_END/d' /tmp/application.yml
fi
# classpath:/ keeps the jar's built-in defaults (incl. the spring.config.import
# property Spring Cloud requires); our rendered file, listed last, overrides them.
export SPRING_CONFIG_LOCATION="classpath:/,file:/tmp/application.yml"
exec java -jar /opt/Lavalink/Lavalink.jar
