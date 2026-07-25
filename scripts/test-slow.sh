#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python}"
KEEP_TEST_DB="${KEEP_TEST_DB:-1}"

keepdb_args=()
if [[ "$KEEP_TEST_DB" == "1" ]]; then
  keepdb_args+=(--keepdb)
fi

# Concurrency and full seed-data checks run sequentially to avoid exhausting
# database connections and CPU on small development machines.
exec "$PYTHON_BIN" manage.py test journal \
  --tag=slow \
  --parallel=1 \
  "${keepdb_args[@]}" \
  "$@"
