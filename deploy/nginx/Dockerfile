ARG NGINX_BASE_IMAGE=nginx:stable-alpine@sha256:47928bf67eb62a1863cb08d9ecb52d3e704304ba614eaee17884204677702951
FROM ${NGINX_BASE_IMAGE}
ARG GIT_SHA=
ARG APP_VERSION=development
LABEL org.opencontainers.image.revision=$GIT_SHA \
    org.opencontainers.image.version=$APP_VERSION \
    org.opencontainers.image.source="https://github.com/zsdadisscy/wanshang"
# The ingress uses Nginx/envsubst and BusyBox HTTP health checks, not curl.
# Remove the unused client and its orphan dependencies without fetching packages.
RUN apk del --no-network curl && ! command -v curl
