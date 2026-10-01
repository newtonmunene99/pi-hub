# syntax=docker/dockerfile:1
FROM python:3.14-alpine

# tzdata so TZ=... gives local times in the schedule and "last run" labels
RUN apk add --no-cache tzdata \
 && adduser -D -u 1000 -h /app pihub \
 && mkdir /config && chown pihub /config

WORKDIR /app
COPY --chown=root:root pihub ./pihub
COPY LICENSE ./

ENV PIHUB_CONFIG=/config/config.json \
    PIHUB_PORT=8000 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER pihub
EXPOSE 8000
VOLUME ["/config"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PIHUB_PORT\",\"8000\")}/api/health', timeout=4)" || exit 1

CMD ["python", "-m", "pihub"]

LABEL org.opencontainers.image.title="pi-hub" \
      org.opencontainers.image.description="Tiny launcher and status dashboard for self-hosted services" \
      org.opencontainers.image.source="https://github.com/newtonmunene99/pi-hub" \
      org.opencontainers.image.licenses="MIT"
