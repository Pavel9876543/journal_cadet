#!/usr/bin/env sh
set -eu

if [ "${WAIT_FOR_DB:-0}" = "1" ]; then
  DB_HOST="${DB_HOST:-db}"
  DB_PORT="${DB_PORT:-5432}"
  DB_WAIT_TIMEOUT="${DB_WAIT_TIMEOUT:-60}"
  elapsed=0

  echo "Waiting for database at ${DB_HOST}:${DB_PORT}..."
  until nc -z "$DB_HOST" "$DB_PORT"; do
    elapsed=$((elapsed + 1))
    if [ "$elapsed" -ge "$DB_WAIT_TIMEOUT" ]; then
      echo "Database did not become available within ${DB_WAIT_TIMEOUT} seconds." >&2
      exit 1
    fi
    sleep 1
  done
fi

MIGRATION_MODE="${MIGRATION_MODE:-check}"
MAKEMIGRATIONS_APP="${MAKEMIGRATIONS_APP:-journal}"

case "$MIGRATION_MODE" in
  create)
    echo "Creating model migrations for app: ${MAKEMIGRATIONS_APP}"
    python manage.py makemigrations --noinput "$MAKEMIGRATIONS_APP"
    ;;
  check)
    echo "Checking that model changes have committed migrations..."
    python manage.py makemigrations --check --dry-run
    ;;
  skip)
    echo "Skipping makemigrations because MIGRATION_MODE=skip."
    ;;
  *)
    echo "Unknown MIGRATION_MODE: ${MIGRATION_MODE}. Use create, check or skip." >&2
    exit 2
    ;;
esac

python manage.py migrate --noinput
python manage.py ensure_superuser

# Static files are generated during the image build. The development bind
# mount intentionally hides them, so the manifest is mandatory only in production.
case "${DJANGO_ENV:-development}" in
  production|prod)
    if [ ! -f "${STATIC_ROOT:-/app/staticfiles}/staticfiles.json" ]; then
      echo "Static manifest is missing from the production image." >&2
      exit 1
    fi
    ;;
esac

exec "$@"
