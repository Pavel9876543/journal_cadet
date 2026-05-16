#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env.prod ]]; then
  echo "Missing .env.prod. Create it from .env.prod.example before production start."
  exit 1
fi

docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml pull

docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml up -d --remove-orphans

echo "Production stack started."
