#!/bin/sh
set -eu

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    echo "Usage: restore-postgres.sh /backups/file.dump" >&2
    exit 2
fi

dump_path="$1"
db_host="${DB_HOST:-db}"
db_port="${DB_PORT:-5432}"
db_name="${DB_NAME:-${POSTGRES_DB:-journal_db}}"
db_user="${DB_USER:-${POSTGRES_USER:-journal_user}}"
db_password="${DB_PASSWORD:-${POSTGRES_PASSWORD:-}}"

if ! pg_restore --list "$dump_path" >/dev/null; then
    echo "The file is not a valid PostgreSQL custom-format archive: $dump_path" >&2
    exit 2
fi

echo "Stop the web service before restoring. Restoring $dump_path into $db_name."
PGPASSWORD="$db_password" pg_restore \
    --host="$db_host" \
    --port="$db_port" \
    --username="$db_user" \
    --dbname="$db_name" \
    --clean \
    --if-exists \
    --exit-on-error \
    --single-transaction \
    --no-owner \
    --no-privileges \
    "$dump_path"

echo "Restore completed successfully: $dump_path"
