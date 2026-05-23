#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

./scripts/ensure-env-files.sh

docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml up -d --build

echo "Local dev started: http://localhost:8000"
