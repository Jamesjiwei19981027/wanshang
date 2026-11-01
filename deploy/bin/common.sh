#!/usr/bin/env bash
# Shared operations for the dedicated production Compose project. Never source .env.
set -euo pipefail

BIN_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
DEPLOY_DIR=$(cd -- "$BIN_DIR/.." && pwd -P)
REPO_DIR=$(cd -- "$DEPLOY_DIR/.." && pwd -P)
COMPOSE_FILE="$DEPLOY_DIR/compose.production.yml"
WRITER_SERVICES=(nginx frontend api celery)
WAS_RUNNING=()

log() { printf '%s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

real_path() { python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' "$1"; }

ops_init() {
    local default_project=$1
    shift
    ENV_FILE=${DEPLOY_ENV_FILE:-"$DEPLOY_DIR/.env.production"}
    PROJECT=$default_project
    local project_explicit=0 env_project
    BACKUP_ROOT=${DEPLOY_BACKUP_ROOT:-"$DEPLOY_DIR/backups"}
    ARGS=()
    while (($#)); do
        case "$1" in
            --env-file) (($# >= 2)) || die '--env-file requires a file'; ENV_FILE=$2; shift 2 ;;
            --project) (($# >= 2)) || die '--project requires a name'; PROJECT=$2; project_explicit=1; shift 2 ;;
            --backup-root) (($# >= 2)) || die '--backup-root requires a directory'; BACKUP_ROOT=$2; shift 2 ;;
            -h|--help)
                printf 'Usage: %s [--env-file FILE] [--project axle-NAME] [--backup-root DIR]%s\n' "$(basename -- "$0")" "${EXTRA_USAGE:-}"
                exit 0 ;;
            --) shift; ARGS+=("$@"); break ;;
            -*) die "Unknown option: $1" ;;
            *) ARGS+=("$1"); shift ;;
        esac
    done
    [[ $(uname -s) == Linux ]] || die 'Run deployment operations on a Linux host.'
    ((BASH_VERSINFO[0] >= 4)) || die 'Bash 4 or later is required.'
    command -v python3 >/dev/null || die 'python3 is required.'
    command -v docker >/dev/null || die 'Docker is required.'
    [[ -f $COMPOSE_FILE ]] || die "Missing Compose file: $COMPOSE_FILE"
    [[ -f $ENV_FILE ]] || die "Missing environment file: $ENV_FILE"
    ENV_FILE=$(real_path "$ENV_FILE")
    BACKUP_ROOT=$(real_path "$BACKUP_ROOT")
    env_project=$(python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import stat, sys
path = Path(sys.argv[1])
mode = stat.S_IMODE(path.stat().st_mode)
if mode & 0o077:
    raise SystemExit('The deployment environment file must have mode 0600 (or stricter).')
projects = []
for raw in path.read_text(encoding='utf-8-sig').splitlines():
    line = raw.strip()
    if line and not line.startswith('#') and '=' in line:
        key, value = line.split('=', 1)
        if key.strip() == 'DEPLOY_PROJECT_NAME':
            projects.append(value.strip().strip('"').strip("'"))
if len(projects) > 1:
    raise SystemExit('Duplicate DEPLOY_PROJECT_NAME entries are forbidden.')
print(projects[0] if projects else '')
PY
    )
    if [[ $default_project == axle-production && -n $env_project ]]; then
        if ((project_explicit == 1)) && [[ $PROJECT != "$env_project" ]]; then
            die '--project must match DEPLOY_PROJECT_NAME in this environment file. Use a separate environment file for another deployment.'
        fi
        PROJECT=$env_project
    fi
    [[ $PROJECT =~ ^axle-[a-z0-9][a-z0-9_-]{1,48}$ ]] || die 'Use an explicit axle-* project name (maximum 54 characters).'
    [[ $(docker info --format '{{.OSType}}') == linux ]] || die 'A Linux Docker engine is required.'
    docker compose version >/dev/null
    umask 077
}

dc() {
    docker compose --project-name "$PROJECT" --project-directory "$DEPLOY_DIR" \
        --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

dc_ops() { dc --profile ops "$@"; }

ops_preflight() { python3 "$DEPLOY_DIR/configure.py" check --env-file "$ENV_FILE"; }

ops_record_release() {
    python3 "$BIN_DIR/release_record.py" --project "$PROJECT" --repository "$REPO_DIR" \
        --output "$RELEASE_RECORD" --phase "$1" --backup "${LATEST_BACKUP:-}"
}

ops_lock() {
    command -v flock >/dev/null || die 'flock (util-linux on the host) is required for deployment operations.'
    local lock_dir="$DEPLOY_DIR/.locks" lock_path
    mkdir -p -- "$lock_dir"
    chmod 700 -- "$lock_dir"
    lock_path="$lock_dir/$PROJECT.lock"
    if [[ ${AXLE_LOCK_PROJECT:-} == "$PROJECT" && -e /proc/$$/fd/9 && $(readlink -- /proc/$$/fd/9) == "$lock_path" ]]; then
        flock -n 9 || die 'The inherited deployment lock could not be verified.'
        return
    fi
    exec 9>"$lock_path"
    flock -n 9 || die "Another deployment/backup operation is active for $PROJECT."
    export AXLE_LOCK_PROJECT="$PROJECT"
}

ops_assert_new_project() {
    local resources
    resources=$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")
    [[ -z $resources ]] || die 'Restore refuses a project that already has containers.'
    resources=$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")
    [[ -z $resources ]] || die 'Restore refuses a project that already has volumes.'
    resources=$(docker network ls -q --filter "label=com.docker.compose.project=$PROJECT")
    [[ -z $resources ]] || die 'Restore refuses a project that already has networks.'
}

ops_stop_writers() {
    local active service oneoffs
    active=$(dc ps --status running --services)
    WAS_RUNNING=()
    for service in "${WRITER_SERVICES[@]}"; do
        if grep -qx -- "$service" <<<"$active"; then WAS_RUNNING+=("$service"); fi
    done
    oneoffs=$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter 'label=com.docker.compose.oneoff=True')
    [[ -z $oneoffs ]] || die 'A one-off operation is running in this project; finish it before a consistent backup.'
    if ((${#WAS_RUNNING[@]})); then
        log "Entering maintenance window for project $PROJECT."
        dc stop --timeout 330 "${WAS_RUNNING[@]}" >&2
    fi
}

ops_resume_writers() {
    if ((${#WAS_RUNNING[@]})); then dc start "${WAS_RUNNING[@]}" >&2; fi
}

ops_media() {
    local operation=$1
    dc_ops run --rm -T --no-deps --entrypoint /app/.venv/bin/python manage \
        -c "$(cat -- "$BIN_DIR/media_archive.py")" "$operation"
}

ops_migration_guard() {
    dc_ops run --rm -T --no-deps --entrypoint /app/.venv/bin/python manage \
        -c "$(cat -- "$BIN_DIR/migration_guard.py")" "$@"
}
