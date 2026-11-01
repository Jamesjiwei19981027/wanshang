#!/bin/sh
set -eu
umask 077
# The official envsubst hook requires its top-level output directory to exist.
# Keep generated configuration on tmpfs while the root filesystem is read-only.
mkdir -p /tmp/nginx-conf
exec /docker-entrypoint.sh "$@"
