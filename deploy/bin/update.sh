#!/usr/bin/env bash
set -euo pipefail
DEPLOY_REQUIRE_EXISTING=1 exec bash "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/start.sh" "$@"
