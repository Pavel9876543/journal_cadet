#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python manage.py migrate
python manage.py seed_data
