#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

./scripts/ensure-env-files.sh

docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml pull

docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml up -d --remove-orphans

echo "Продакшен-стек запущен."
