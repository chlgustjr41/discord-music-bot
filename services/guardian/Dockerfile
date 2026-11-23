FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
# Dependencies are installed as root, BEFORE the switch to the runtime user:
# site-packages stays root-owned, so the running process cannot modify its
# own code. pip byte-compiles here, at build time.
RUN pip install --no-cache-dir .
# Unprivileged runtime user with a FIXED uid/gid (same number in every image
# of this stack). The guardian needs no file or socket access of its own: it
# reaches Docker through the socket-proxy service over TCP.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app
# Unbuffered logs so `docker logs` is real-time. No bytecode writes at
# runtime: the container's root filesystem is read-only (deploy/docker-compose.yml).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
CMD ["python", "-m", "guardian"]
