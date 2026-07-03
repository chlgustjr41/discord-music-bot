FROM ghcr.io/lavalink-devs/lavalink:4
USER root
COPY application.yml.tmpl /opt/Lavalink/application.yml.tmpl
COPY entrypoint.sh /opt/Lavalink/entrypoint.sh
RUN chmod +x /opt/Lavalink/entrypoint.sh
USER lavalink
ENTRYPOINT ["/opt/Lavalink/entrypoint.sh"]
