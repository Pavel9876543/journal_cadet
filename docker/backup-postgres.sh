#!/bin/sh
set -eu

backup_dir="${BACKUP_DIR:-/backups}"
retention_days="${BACKUP_RETENTION_DAYS:-30}"
interval_seconds="${BACKUP_INTERVAL_SECONDS:-86400}"
db_host="${DB_HOST:-db}"
db_port="${DB_PORT:-5432}"
db_name="${DB_NAME:-${POSTGRES_DB:-journal_db}}"
db_user="${DB_USER:-${POSTGRES_USER:-journal_user}}"
db_password="${DB_PASSWORD:-${POSTGRES_PASSWORD:-}}"

run_backup() {
    mkdir -p "$backup_dir"
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    final_path="$backup_dir/${db_name}_${timestamp}.dump"
    temporary_path="${final_path}.tmp"

    PGPASSWORD="$db_password" pg_dump \
        --host="$db_host" \
        --port="$db_port" \
        --username="$db_user" \
        --dbname="$db_name" \
        --format=custom \
        --no-owner \
        --no-privileges \
        --file="$temporary_path"

    mv "$temporary_path" "$final_path"
    find "$backup_dir" -type f -name "${db_name}_*.dump" -mtime "+$retention_days" -delete
    echo "Backup created: $final_path"
}

if [ "${1:-}" = "--loop" ]; then
    while true; do
        run_backup
        sleep "$interval_seconds"
    done
fi

run_backup
