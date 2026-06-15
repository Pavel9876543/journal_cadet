#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

output="export.csv"

if [[ -x ".venv/bin/python" ]]; then
  python_bin=".venv/bin/python"
else
  python_bin="python3"
fi

"$python_bin" manage.py export_temporary_credentials --output "$output"

echo "Экспорт выполнен: $output"
nano "$output"
