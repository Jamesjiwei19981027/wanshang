#!/usr/bin/env bash
# First deployment and safe updates share one path; no demo seeding or fake migrations.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"
ops_init axle-production "$@"
((${#ARGS[@]} == 0)) || die 'start.sh accepts no positional arguments.'
ops_lock
ops_preflight
if [[ ${DEPLOY_REQUIRE_EXISTING:-0} == 1 ]]; then
    [[ -n $(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT") ]] || die 'update.sh requires an existing project; use start.sh for a first deployment.'
fi
MIGRATION_STARTED=0
INFRASTRUCTURE_STARTED=0
COMPLETED=0
LATEST_BACKUP=''
RELEASE_RECORD="$DEPLOY_DIR/releases/$PROJECT/$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM.json"
finish() {
    local status=$?
    trap - EXIT
    if ((COMPLETED == 0)); then
        if ((MIGRATION_STARTED == 0 && INFRASTRUCTURE_STARTED == 0)); then
            ops_resume_writers || status=1
            ops_record_release failed-before-migrate || log 'Could not append the failed deployment state.'
        else
            dc stop --timeout 330 nginx frontend api celery >&2 || true
            if ((MIGRATION_STARTED == 1)); then
                ops_record_release failed-after-migrate || log 'Could not append the failed deployment state.'
            else
                ops_record_release failed-after-infrastructure || log 'Could not append the failed deployment state.'
            fi
            log 'Deployment stopped after infrastructure updates or migrations began. Application services remain stopped; do not blindly roll back the database or image.'
            log "Inspect the migration and restore plan. Pre-deployment backup: $LATEST_BACKUP"
        fi
    fi
    exit "$status"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
ops_record_release prepared
dc build api frontend nginx >&2
ops_stop_writers
EXISTING_POSTGRES=$(dc ps --all --quiet postgres)
if [[ -n $EXISTING_POSTGRES ]]; then
    # Start the existing container with its existing environment before backup.
    # Recreating infrastructure first could invalidate credentials or mutate data.
    dc start postgres >&2
    POSTGRES_READY=0
    for ((attempt=0; attempt<60; attempt++)); do
        if dc exec -T postgres sh -ceu 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then POSTGRES_READY=1; break; fi
        sleep 1
    done
    ((POSTGRES_READY == 1)) || die 'Existing PostgreSQL did not become ready for its pre-update backup.'
else
    dc up -d --wait --wait-timeout 120 postgres >&2
    dc --profile ops up --no-deps --abort-on-container-exit --exit-code-from volume-init volume-init >&2
fi
LATEST_BACKUP=$(bash "$BIN_DIR/backup.sh" --env-file "$ENV_FILE" --project "$PROJECT" --backup-root "$BACKUP_ROOT")
ops_migration_guard
INFRASTRUCTURE_STARTED=1
dc up -d --wait --wait-timeout 900 postgres redis clamav >&2
dc --profile ops up --no-deps --abort-on-container-exit --exit-code-from volume-init volume-init >&2
MIGRATION_STARTED=1
dc_ops run --rm -T --no-deps manage migrate --noinput >&2
ops_migration_guard --expect-current
dc_ops run --rm -T --no-deps manage collectstatic --noinput >&2
dc_ops run --rm -T --no-deps manage check --deploy --fail-level WARNING >&2
dc up -d --wait --wait-timeout 900 api celery frontend nginx >&2
ops_record_release healthy
COMPLETED=1
log "Production services are healthy for project $PROJECT."
log 'Complete the public HTTPS, Turnstile and real-delivery acceptance on the target host.'
log "Pre-deployment backup: $LATEST_BACKUP"
log "Release image identities and backup association: $RELEASE_RECORD"
dc ps
