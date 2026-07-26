#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
TEST_PROCESSES="${TEST_PROCESSES:-2}"
KEEP_TEST_DB="${KEEP_TEST_DB:-1}"

keepdb_args=()
if [[ "$KEEP_TEST_DB" == "1" ]]; then
  keepdb_args+=(--keepdb)
fi

exec "$PYTHON_BIN" manage.py test journal \
  --settings=config.test_settings \
  --parallel="$TEST_PROCESSES" \
  "${keepdb_args[@]}" \
  "$@"

