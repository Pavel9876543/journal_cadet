#!/usr/bin/env sh
set -eu

if [ "${WAIT_FOR_DB:-0}" = "1" ]; then
  DB_HOST="${DB_HOST:-db}"
  DB_PORT="${DB_PORT:-5432}"
  echo "Waiting for database at ${DB_HOST}:${DB_PORT}..."
  until nc -z "$DB_HOST" "$DB_PORT"; do
    sleep 1
  done
fi

python manage.py migrate --noinput
python manage.py ensure_superuser

STATIC_ROOT="${STATIC_ROOT:-/var/lib/cadet-journal/staticfiles}"
if ! mkdir -p "$STATIC_ROOT" 2>/dev/null || [ ! -w "$STATIC_ROOT" ]; then
  echo "Static files directory is not writable: $STATIC_ROOT" >&2
  echo "Do not place STATIC_ROOT inside a Windows bind mount; use a container-writable path." >&2
  exit 1
fi
python manage.py collectstatic --noinput --clear

exec "$@"
