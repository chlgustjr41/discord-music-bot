FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libopus0 unzip curl && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /tmp/model.zip \
        https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip \
    && unzip -q /tmp/model.zip -d /models \
    && mv /models/vosk-model-small-en-us-0.15 /models/vosk-small-en \
    && rm /tmp/model.zip
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY assets ./assets
RUN pip install --no-cache-dir .
RUN python -c "import ears.gateway, ears.api"    # import smoke at build time
# Unbuffered logs so `docker logs` is real-time
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "ears"]
