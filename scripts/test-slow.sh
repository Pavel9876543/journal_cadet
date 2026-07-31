#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
KEEP_TEST_DB="${KEEP_TEST_DB:-0}"

keepdb_args=()
if [[ "$KEEP_TEST_DB" == "1" ]]; then
  keepdb_args+=(--keepdb)
fi

export DJANGO_ENV="${DJANGO_ENV:-test}"
export DJANGO_ENV_FILE="${DJANGO_ENV_FILE:-.env.test}"

# Concurrency and full seed-data scenarios are deliberately serial.
exec "$PYTHON_BIN" manage.py test journal \
  --settings=config.test_settings \
  --tag=slow \
  --parallel=1 \
  "${keepdb_args[@]}" \
  "$@"
