#!/usr/bin/env bash
# Stop only; retain all containers, database volumes and media.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"
ops_init axle-production "$@"
((${#ARGS[@]} == 0)) || die 'stop.sh accepts no positional arguments.'
ops_lock
dc stop --timeout 330 nginx frontend api celery clamav redis postgres
