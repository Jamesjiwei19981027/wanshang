# Official Python 3.13.18 / supported Alpine 3.24, amd64 and arm64 manifests
# verified on 2026-10-31. Keep the digest and native package policy in sync.
FROM python:3.13.18-alpine3.24@sha256:7984cf56ceaddebe4fefeb75d64000b9f45d8b77b11a09b24524da6cdc3af0c8 AS base
COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /uvx /usr/local/bin/

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON=/usr/local/bin/python \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_SYNC=1 \
    PSYCOPG_IMPL=c

FROM base AS builder
# Compilers and headers exist only in this discarded stage. The libpq C
# binding avoids runtime ctypes library-discovery tools on musl.
RUN for attempt in 1 2 3; do apk add --no-cache build-base \
    libpq-dev=18.6-r0 libjpeg-turbo-dev=3.1.3-r0 \
    zlib-dev=1.3.2-r1 libwebp-dev=1.6.0-r0 \
    && break; [ "$attempt" != 3 ] || exit 1; sleep 1; done
COPY pyproject.toml uv.lock ./
# Build the locked Pillow sdist with only the native codecs this application
# needs. Do not import private TIFF/JPEG2000/AVIF/C++ wheel libraries.
RUN uv sync --locked --no-dev --extra container --python /usr/local/bin/python --no-binary-package pillow \
    --config-settings-package pillow:jpeg=enable \
    --config-settings-package pillow:zlib=enable \
    --config-settings-package pillow:webp=enable \
    --config-settings-package pillow:tiff=disable \
    --config-settings-package pillow:jpeg2000=disable \
    --config-settings-package pillow:lcms=disable \
    --config-settings-package pillow:freetype=disable \
    --config-settings-package pillow:raqm=disable \
    --config-settings-package pillow:imagequant=disable \
    --config-settings-package pillow:xcb=disable \
    --config-settings-package pillow:avif=disable

FROM base AS runtime
ARG GIT_SHA=
ARG APP_VERSION=development
LABEL org.opencontainers.image.revision=$GIT_SHA \
    org.opencontainers.image.version=$APP_VERSION \
    org.opencontainers.image.source="https://github.com/zsdadisscy/wanshang"
# Alpine's system libpq has no GSS/LDAP dependency. The zlib -r1 package carries
# the CVE-2026-85091 fix; distro OpenSSL stays separate from binary wheels.
RUN for attempt in 1 2 3; do apk add --no-cache \
    libpq=18.6-r0 libssl3=3.5.9-r0 libcrypto3=3.5.9-r0 \
    libjpeg-turbo=3.1.3-r0 libwebp=1.6.0-r0 \
    libwebpdemux=1.6.0-r0 libwebpmux=1.6.0-r0 zlib=1.3.2-r1 \
    && break; [ "$attempt" != 3 ] || exit 1; sleep 1; done
COPY --from=builder /app/.venv /app/.venv
# Copy only application sources. Never import a host .venv, .env, or media store.
COPY pyproject.toml uv.lock .python-version manage.py ./
COPY catalog ./catalog
COPY assistant ./assistant
COPY config ./config
COPY templates ./templates
RUN addgroup -g 10001 app && adduser -D -u 10001 -G app -s /sbin/nologin app \
    && mkdir -p /app/media /app/private_media \
    && chmod 0700 /app/private_media \
    && chown -R app:app /app

USER app
# Verify actual codecs, linked libraries and package versions in the final
# stage, after the compiler and optional native dependencies have disappeared.
RUN uv run python config/runtime_security.py

CMD ["uv", "run", "celery", "-A", "config", "worker", "--loglevel=INFO"]
