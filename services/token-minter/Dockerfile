FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
# Dependencies are installed as root, BEFORE the switch to the runtime user:
# site-packages stays root-owned, so the running process cannot modify its
# own code. pip byte-compiles here, at build time.
RUN pip install --no-cache-dir .
# Unprivileged runtime user with a FIXED uid/gid: the ownership of the shared
# `tokens` volume keys on the number (see below).
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app
# Create the tokens dir in the IMAGE, owned by the runtime user. Docker seeds
# a new named volume from the image path and copies its ownership — but only
# if the path exists. Without this the volume is created root-owned and the
# unprivileged process cannot write tokens.env into it. (Same trick as the
# plugins dir in services/lavalink/Dockerfile.)
# The minter writes tokens.env via temp-file + rename, mode 0644; Lavalink
# (uid 322) mounts the volume read-only and only reads it.
# A volume created BEFORE this image ran as non-root stays root-owned — fix
# it once with:
#   docker run --rm -v jacky-music_tokens:/t alpine chown -R 10001:10001 /t
RUN mkdir -p /data/tokens && chown 10001:10001 /data/tokens
# Unbuffered logs so `docker logs` is real-time. No bytecode writes at
# runtime: the container's root filesystem is read-only (deploy/docker-compose.yml).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
CMD ["python", "-m", "minter"]
