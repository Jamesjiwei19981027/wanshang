#!/usr/bin/env bash
# Consistent PostgreSQL + public/private media snapshot; suitable for cron.
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"
ops_init axle-production "$@"
((${#ARGS[@]} == 0)) || die 'backup.sh accepts no positional arguments.'
ops_lock
python3 "$BIN_DIR/backup_manifest.py" prepare-root "$BACKUP_ROOT" >&2
STAGING=$(mktemp -d "$BACKUP_ROOT/.incomplete-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
COMPLETE=0
finish() {
    local status=$?
    trap - EXIT
    if ! ops_resume_writers; then status=1; log 'Could not resume all previously running services; inspect compose status.'; fi
    if ((COMPLETE == 0)); then log "Incomplete backup retained for inspection: $STAGING"; fi
    exit "$status"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
ops_stop_writers
log 'Creating database snapshot without exposing credentials in host arguments.'
dc exec -T postgres sh -ceu 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec pg_dump --format=custom --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >"$STAGING/database.dump"
log 'Archiving public and private media while all application writers are stopped.'
ops_media dump >"$STAGING/media.tar.gz"
python3 "$BIN_DIR/backup_manifest.py" create "$STAGING" --project "$PROJECT" --repository "$REPO_DIR" >&2
python3 "$BIN_DIR/backup_manifest.py" verify "$STAGING" >&2
BACKUP_NAME=${STAGING##*/}
FINAL="$BACKUP_ROOT/backup-${BACKUP_NAME#.incomplete-}"
[[ ! -e $FINAL ]] || die 'Backup destination unexpectedly exists.'
mv -- "$STAGING" "$FINAL"
COMPLETE=1
log "Backup complete: $FINAL"
printf '%s\n' "$FINAL"
