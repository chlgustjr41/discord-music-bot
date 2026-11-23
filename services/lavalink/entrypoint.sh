#!/bin/sh
set -eu
: "${YOUTUBE_PLUGIN_VERSION:?YOUTUBE_PLUGIN_VERSION must be set (see deploy/.env.example)}"
case "$YOUTUBE_PLUGIN_VERSION" in
  *[!A-Za-z0-9._-]*) echo "YOUTUBE_PLUGIN_VERSION contains invalid characters" >&2; exit 1 ;;
esac
# A 40-hex version is a youtube-source commit hash: pull it from the snapshot
# repo (upstream publishes one build per master commit). Anything else is a
# release tag. Lets deploy/.env hotfix to an unreleased commit when YouTube
# breaks the newest release (playbook F3).
case "$YOUTUBE_PLUGIN_VERSION" in
  *[!0-9a-f]*)
    PLUGIN_REPOSITORY="https://maven.lavalink.dev/releases"; PLUGIN_SNAPSHOT=false ;;
  ????????????????????????????????????????)
    PLUGIN_REPOSITORY="https://maven.lavalink.dev/snapshots"; PLUGIN_SNAPSHOT=true ;;
  *)
    PLUGIN_REPOSITORY="https://maven.lavalink.dev/releases"; PLUGIN_SNAPSHOT=false ;;
esac
sed "s|__YOUTUBE_PLUGIN_VERSION__|${YOUTUBE_PLUGIN_VERSION}|g; \
     s|__YOUTUBE_PLUGIN_REPOSITORY__|${PLUGIN_REPOSITORY}|g; \
     s|__YOUTUBE_PLUGIN_SNAPSHOT__|${PLUGIN_SNAPSHOT}|g" \
    /opt/Lavalink/application.yml.tmpl > /tmp/application.yml

# Local-jar mode (YOUTUBE_PLUGIN_SHA256 set): fetch the plugin from GitHub
# releases instead of letting Lavalink resolve it from maven.lavalink.dev.
#
# Why this exists: the maven `dependency:` block downloads into
# /opt/Lavalink/plugins at every boot. That directory used to be ephemeral, so
# recreating the container discarded the jar and made the service unbootable
# whenever maven.lavalink.dev was unreachable — which is exactly what took
# playback down on 2026-08-09. The dir is now a named volume AND the jar has a
# second, independent source. Lavalink loads any jar it finds in the plugins
# directory, so the declarative block is deleted in this mode.
#
# The hash is mandatory, not optional: without it this would execute whatever
# bytes the URL served. Same discipline as the plugin's bundled ffmpeg.
if [ -n "${YOUTUBE_PLUGIN_SHA256:-}" ]; then
  PLUGIN_DIR=/opt/Lavalink/plugins
  JAR="${PLUGIN_DIR}/youtube-plugin-${YOUTUBE_PLUGIN_VERSION}.jar"
  mkdir -p "$PLUGIN_DIR"
  if [ ! -f "$JAR" ]; then
    echo "fetching youtube-plugin ${YOUTUBE_PLUGIN_VERSION} from GitHub releases"
    curl -fsSL --retry 3 --retry-delay 5 -o "${JAR}.tmp" \
      "https://github.com/lavalink-devs/youtube-source/releases/download/${YOUTUBE_PLUGIN_VERSION}/youtube-plugin-${YOUTUBE_PLUGIN_VERSION}.jar"
    mv "${JAR}.tmp" "$JAR"
  fi
  # Verified on EVERY boot, not only right after a download. The jar lives on
  # a persistent volume: checking it once and trusting it forever would keep
  # executing a file that has since rotted or been swapped on that volume, and
  # would ignore a YOUTUBE_PLUGIN_SHA256 that changed after the first fetch.
  # Fail closed: a mismatched hash leaves no jar behind rather than running
  # an unverified one (the next start then fetches a fresh copy).
  if ! echo "${YOUTUBE_PLUGIN_SHA256}  ${JAR}" | sha256sum -c -; then
    rm -f "$JAR"
    echo "youtube-plugin checksum mismatch; refusing to start" >&2
    exit 1
  fi
  # Drop any OTHER youtube-plugin jars so a version change doesn't leave two
  # on the classpath — Lavalink would load both.
  for old in "$PLUGIN_DIR"/youtube-plugin-*.jar; do
    [ "$old" = "$JAR" ] || [ ! -f "$old" ] || rm -f "$old"
  done
  sed -i '/# PLUGIN_BLOCK_START/,/# PLUGIN_BLOCK_END/d' /tmp/application.yml
fi
# Cold-start poToken injection: the token-minter persists tokens.env to the
# shared volume. The file is PARSED, never sourced — sourcing it would run
# whatever that volume holds as shell, as this container's user, on every
# boot. Values are charset-guarded twice: by the minter at write time and
# again here before use (ADR-0004: websafe base64 / URL-encoded, may contain
# '%'), so the '|'-delimited sed below and the double-quoted YAML scalar it
# fills are safe.
TOKENS_FILE="${TOKENS_FILE:-/data/tokens/tokens.env}"
inject_tokens=false
POT_TOKEN=""
POT_VISITOR_DATA=""
# usable_token NAME VALUE — true only for a non-empty VALUE made solely of
# [A-Za-z0-9=%._~+/-]. Logs the key NAME on refusal, never the value.
usable_token() {
  case "$2" in
    "")
      return 1 ;;
    *[!A-Za-z0-9=%._~+/-]*)
      echo "tokens.env: $1 contains unexpected characters; ignoring the tokens file" >&2
      return 1 ;;
  esac
  return 0
}
# Freshness gate: an expired poToken (provider down >TTL, then a restart) is
# worse than none — token-bearing requests get hard-rejected, tokenless just
# degrades. 330 min matches the minter's 5.5h refresh cadence.
if [ -f "$TOKENS_FILE" ] && [ -n "$(find "$TOKENS_FILE" -mmin -330 2>/dev/null)" ]; then
  # `|| true`: an unreadable file means "no tokens", not a dead audio core.
  POT_TOKEN=$(sed -n 's/^POT_TOKEN=//p' "$TOKENS_FILE") || true
  POT_VISITOR_DATA=$(sed -n 's/^POT_VISITOR_DATA=//p' "$TOKENS_FILE") || true
  if usable_token POT_TOKEN "$POT_TOKEN" && usable_token POT_VISITOR_DATA "$POT_VISITOR_DATA"; then
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
