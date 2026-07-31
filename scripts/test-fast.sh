#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
TEST_PROCESSES="${TEST_PROCESSES:-2}"
KEEP_TEST_DB="${KEEP_TEST_DB:-0}"

keepdb_args=()
if [[ "$KEEP_TEST_DB" == "1" ]]; then
  keepdb_args+=(--keepdb)
fi

export DJANGO_ENV="${DJANGO_ENV:-test}"
export DJANGO_ENV_FILE="${DJANGO_ENV_FILE:-.env.test}"

exec "$PYTHON_BIN" manage.py test journal \
  --settings=config.test_settings \
  --exclude-tag=slow \
  --parallel="$TEST_PROCESSES" \
  "${keepdb_args[@]}" \
  "$@"
