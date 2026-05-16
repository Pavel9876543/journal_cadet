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
python manage.py collectstatic --noinput

exec "$@"
