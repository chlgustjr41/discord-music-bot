FROM ghcr.io/lavalink-devs/lavalink:4
USER root
COPY application.yml.tmpl /opt/Lavalink/application.yml.tmpl
COPY entrypoint.sh /opt/Lavalink/entrypoint.sh
RUN chmod +x /opt/Lavalink/entrypoint.sh
# Create the plugins dir in the IMAGE, owned by the runtime user. Docker seeds
# a new named volume from the image path and copies its ownership — but only
# if the path exists. Without this the volume is created root-owned and the
# unprivileged process cannot write the plugin jar into it.
RUN mkdir -p /opt/Lavalink/plugins && chown lavalink:lavalink /opt/Lavalink/plugins
USER lavalink
ENTRYPOINT ["/opt/Lavalink/entrypoint.sh"]
