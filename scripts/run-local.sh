#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

./scripts/ensure-env-files.sh .env.dev

if docker info >/dev/null 2>&1; then
  DOCKER_CMD=(docker)
elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
  DOCKER_CMD=(sudo -n docker)
else
  echo "Ошибка: Docker недоступен для текущего пользователя."
  echo "Проверьте установку Docker или права пользователя."
  exit 1
fi

"${DOCKER_CMD[@]}" compose \
  --env-file .env.dev \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  up -d --build --remove-orphans

echo "Локальный запуск выполнен: http://localhost:8000"
