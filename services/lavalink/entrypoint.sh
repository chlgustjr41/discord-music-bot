#!/bin/sh
set -eu
: "${YOUTUBE_PLUGIN_VERSION:?YOUTUBE_PLUGIN_VERSION must be set (see deploy/.env.example)}"
sed "s|__YOUTUBE_PLUGIN_VERSION__|${YOUTUBE_PLUGIN_VERSION}|g" \
    /opt/Lavalink/application.yml.tmpl > /tmp/application.yml
# classpath:/ keeps the jar's built-in defaults (incl. the spring.config.import
# property Spring Cloud requires); our rendered file, listed last, overrides them.
export SPRING_CONFIG_LOCATION="classpath:/,file:/tmp/application.yml"
exec java -jar /opt/Lavalink/Lavalink.jar
