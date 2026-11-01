#!/usr/bin/env bash
# Restore into a completely new project. Never overwrite a running or existing installation.
set -euo pipefail
EXTRA_USAGE=' BACKUP_DIRECTORY'
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"
ops_init "axle-restore-$(date -u +%Y%m%d%H%M%S)-$RANDOM" "$@"
((${#ARGS[@]} == 1)) || die 'Supply exactly one backup directory.'
ops_lock
BACKUP=$(real_path "${ARGS[0]}")
python3 "$BIN_DIR/backup_manifest.py" verify "$BACKUP" >&2
ops_assert_new_project
log "Restoring only into new isolated project: $PROJECT"
dc up -d --wait --wait-timeout 120 postgres >&2
dc --profile ops up --no-deps --abort-on-container-exit --exit-code-from volume-init volume-init >&2
TABLE_COUNT=$(dc exec -T postgres sh -ceu 'export PGPASSWORD="$POSTGRES_PASSWORD"; psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('"'"'pg_catalog'"'"','"'"'information_schema'"'"') AND n.nspname NOT LIKE '"'"'pg_toast%'"'"' AND c.relkind IN ('"'"'r'"'"','"'"'p'"'"','"'"'S'"'"','"'"'v'"'"','"'"'m'"'"','"'"'f'"'"');"')
[[ $TABLE_COUNT == 0 ]] || die 'Restore refuses a non-empty target database.'
ops_media check-empty >&2
dc exec -T postgres sh -ceu 'export PGPASSWORD="$APP_DB_PASSWORD"; exec pg_restore --exit-on-error --no-owner --no-privileges --role "$APP_DB_USER" -h 127.0.0.1 -U "$APP_DB_USER" -d "$POSTGRES_DB"' <"$BACKUP/database.dump"
ops_media restore <"$BACKUP/media.tar.gz" >&2
ops_migration_guard --expect-current >&2
log "Restore complete in isolated project $PROJECT. API, workers and ingress were not started."
log 'Validate records and media before planning a controlled cutover; this script does not overwrite the original project or replay notifications.'
printf '%s\n' "$PROJECT"
