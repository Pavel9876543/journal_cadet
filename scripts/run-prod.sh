#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

./scripts/ensure-env-files.sh .env.prod

docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml up -d --build --remove-orphans

echo "Продакшен-стек собран и запущен."
