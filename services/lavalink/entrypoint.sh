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
inject_tokens=false
# Freshness gate: an expired poToken (provider down >TTL, then a restart) is
# worse than none — token-bearing requests get hard-rejected, tokenless just
# degrades. 330 min matches the minter's 5.5h refresh cadence.
if [ -f "$TOKENS_FILE" ] && [ -n "$(find "$TOKENS_FILE" -mmin -330 2>/dev/null)" ]; then
  . "$TOKENS_FILE"
  if [ -n "${POT_TOKEN:-}" ] && [ -n "${POT_VISITOR_DATA:-}" ]; then
    inject_tokens=true
  fi
fi
if [ "$inject_tokens" = true ]; then
  sed -i "s|__POT_TOKEN__|${POT_TOKEN}|g; s|__POT_VISITOR_DATA__|${POT_VISITOR_DATA}|g" /tmp/application.yml
else
  # No fresh, complete tokens: run tokenless (known-degraded M1 state) rather
  # than injecting garbage or crash-looping the audio core.
  sed -i '/# POT_BLOCK_START/,/# POT_BLOCK_END/d' /tmp/application.yml
fi
# classpath:/ keeps the jar's built-in defaults (incl. the spring.config.import
# property Spring Cloud requires); our rendered file, listed last, overrides them.
export SPRING_CONFIG_LOCATION="classpath:/,file:/tmp/application.yml"
exec java -jar /opt/Lavalink/Lavalink.jar
