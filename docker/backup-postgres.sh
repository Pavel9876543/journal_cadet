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

require_non_negative_integer() {
    value="$1"
    variable_name="$2"
    case "$value" in
        ''|*[!0-9]*)
            echo "$variable_name must be a non-negative integer, got: $value" >&2
            exit 2
            ;;
    esac
}

require_positive_integer() {
    value="$1"
    variable_name="$2"
    require_non_negative_integer "$value" "$variable_name"
    if [ "$value" -eq 0 ]; then
        echo "$variable_name must be greater than zero." >&2
        exit 2
    fi
}

require_non_negative_integer "$retention_days" BACKUP_RETENTION_DAYS
require_positive_integer "$interval_seconds" BACKUP_INTERVAL_SECONDS

run_backup() {
    mkdir -p "$backup_dir"
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    final_path="$backup_dir/${db_name}_${timestamp}.dump"
    temporary_path="${final_path}.tmp"

    rm -f "$temporary_path"
    if ! PGPASSWORD="$db_password" pg_dump \
        --host="$db_host" \
        --port="$db_port" \
        --username="$db_user" \
        --dbname="$db_name" \
        --format=custom \
        --no-owner \
        --no-privileges \
        --file="$temporary_path"; then
        rm -f "$temporary_path"
        echo "Backup failed; incomplete file removed: $temporary_path" >&2
        return 1
    fi

    if ! pg_restore --list "$temporary_path" >/dev/null; then
        rm -f "$temporary_path"
        echo "Backup verification failed; incomplete file removed: $temporary_path" >&2
        return 1
    fi

    mv "$temporary_path" "$final_path"
    find "$backup_dir" -type f -name "${db_name}_*.dump" -mtime "+$retention_days" -delete
    echo "Backup created and verified: $final_path"
}

if [ "${1:-}" = "--loop" ]; then
    while true; do
        run_backup
        sleep "$interval_seconds"
    done
fi

run_backup
