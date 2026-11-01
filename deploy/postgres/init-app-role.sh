#!/bin/sh
# Executed only by the official PostgreSQL entrypoint for a NEW dedicated cluster.
set +x
set -eu
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${APP_DB_USER:?APP_DB_USER is required}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD is required}"
[ "$APP_DB_USER" != "$POSTGRES_USER" ] || { echo 'Application and initialization roles must be different.' >&2; exit 1; }
case "$APP_DB_USER" in pg_*) echo 'APP_DB_USER must not use a reserved pg_ role name.' >&2; exit 1;; esac
case "$APP_DB_USER" in ''|*[!a-z0-9_]*) echo 'APP_DB_USER must contain only lowercase letters, digits and underscores.' >&2; exit 1;; esac
case "$APP_DB_USER" in [a-z_]*) ;; *) echo 'APP_DB_USER must begin with a lowercase letter or underscore.' >&2; exit 1;; esac
[ "${#APP_DB_USER}" -le 63 ] || { echo 'APP_DB_USER exceeds PostgreSQL identifier length.' >&2; exit 1; }
[ "${#APP_DB_PASSWORD}" -ge 32 ] || { echo 'APP_DB_PASSWORD must contain at least 32 characters.' >&2; exit 1; }
[ "$APP_DB_PASSWORD" != "${POSTGRES_PASSWORD:-}" ] || { echo 'Application and initialization passwords must be different.' >&2; exit 1; }
psql -X --quiet --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv app_user APP_DB_USER
\getenv app_password APP_DB_PASSWORD
\getenv app_database POSTGRES_DB
SET password_encryption = 'scram-sha-256';
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION PASSWORD %L', :'app_user', :'app_password') \gexec
SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', datname) FROM pg_database WHERE datallowconn \gexec
GRANT CONNECT ON DATABASE :"app_database" TO :"app_user";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO :"app_user";
GRANT USAGE, CREATE ON SCHEMA public TO :"app_user";
SQL
