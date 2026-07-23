#!/bin/sh
set -eu

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    echo "Usage: restore-postgres.sh /backups/file.dump" >&2
    exit 2
fi

db_host="${DB_HOST:-db}"
db_port="${DB_PORT:-5432}"
db_name="${DB_NAME:-${POSTGRES_DB:-journal_db}}"
db_user="${DB_USER:-${POSTGRES_USER:-journal_user}}"
db_password="${DB_PASSWORD:-${POSTGRES_PASSWORD:-}}"

echo "Stop the web service before restoring. Restoring $1 into $db_name."
PGPASSWORD="$db_password" pg_restore \
    --host="$db_host" \
    --port="$db_port" \
    --username="$db_user" \
    --dbname="$db_name" \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    "$1"
