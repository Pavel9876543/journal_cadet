#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env.dev ]]; then
  cp .env.dev.example .env.dev
  echo "Created .env.dev from .env.dev.example. Edit it if needed."
fi

docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml up -d --build

echo "Local dev started: http://localhost:8000"
