FROM rclone/rclone:1.75.0 AS rclone-binary


FROM python:3.13-slim AS backend-builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY pyproject.toml ./
COPY src ./src
COPY scripts/export_openapi.py ./scripts/export_openapi.py

RUN python -m pip wheel --wheel-dir /wheels . \
    && python -m pip install /wheels/* \
    && python scripts/export_openapi.py --output generated/openapi.json


FROM node:24-alpine AS frontend-builder

WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY --from=backend-builder /build/generated/openapi.json /build/generated/openapi.json
COPY frontend ./
RUN npm run build


FROM python:3.13-slim AS runtime

ENV ECHO_CONFIG_FILE=/config/config.yml \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --no-install-recommends --yes ca-certificates tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 echo \
    && useradd --uid 1000 --gid echo --create-home echo \
    && mkdir -p /app /config/rclone /data /opt/echo/scripts /srv/echo/frontend \
    && chown -R echo:echo /app /config /data /opt/echo /srv/echo

COPY --from=rclone-binary /usr/local/bin/rclone /usr/local/bin/rclone

COPY --from=backend-builder /wheels /wheels
RUN python -m pip install /wheels/* && rm -rf /wheels

COPY --from=frontend-builder --chown=echo:echo /build/frontend/dist/ /srv/echo/frontend/
COPY --chown=echo:echo scripts/upgrade_database.py /opt/echo/scripts/upgrade_database.py
COPY docker/entrypoint.sh /usr/local/bin/echo-entrypoint
RUN chmod 0755 /usr/local/bin/echo-entrypoint

USER echo
WORKDIR /app

VOLUME ["/config", "/data"]
EXPOSE 8000 5173

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c \
        "import urllib.request as u; " \
        "u.urlopen('http://127.0.0.1:8000/api/health/ready', timeout=3).read(); " \
        "u.urlopen('http://127.0.0.1:5173/', timeout=3).read(1)"

STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/echo-entrypoint"]
CMD ["serve"]
