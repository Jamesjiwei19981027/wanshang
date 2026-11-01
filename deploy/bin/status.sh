#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"
ops_init axle-production "$@"
((${#ARGS[@]} == 0)) || die 'status.sh accepts no positional arguments.'
dc ps --all
