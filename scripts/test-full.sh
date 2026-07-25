#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
TEST_PROCESSES="${TEST_PROCESSES:-1}"
KEEP_TEST_DB="${KEEP_TEST_DB:-1}"

keepdb_args=()
if [[ "$KEEP_TEST_DB" == "1" ]]; then
  keepdb_args+=(--keepdb)
fi

# One process is the safe default because the suite includes a real
# PostgreSQL concurrency scenario and a destructive seed-data command.
exec "$PYTHON_BIN" manage.py test journal \
  --parallel="$TEST_PROCESSES" \
  "${keepdb_args[@]}" \
  "$@"
