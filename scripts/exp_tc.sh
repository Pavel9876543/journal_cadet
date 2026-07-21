#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

output="export.csv"

if [ ! -f ".env.prod" ]; then
  echo "Ошибка: файл .env.prod не найден в корне проекта."
  exit 1
fi

if docker info >/dev/null 2>&1; then
  DOCKER_CMD=(docker)
elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
  DOCKER_CMD=(sudo -n docker)
else
  echo "Ошибка: Docker недоступен."
  exit 1
fi

COMPOSE_CMD=(
  "${DOCKER_CMD[@]}"
  compose
  --env-file .env.prod
  -f docker-compose.yml
  -f docker-compose.prod.yml
)

echo "=== Экспорт временных учетных данных ==="

"${COMPOSE_CMD[@]}" exec -T web \
  python manage.py export_student_credentials_with_phone \
  --output "/tmp/$output"

container_id="$("${COMPOSE_CMD[@]}" ps -q web)"

if [ -z "$container_id" ]; then
  echo "Ошибка: контейнер web не найден."
  exit 1
fi

"${DOCKER_CMD[@]}" cp "$container_id:/tmp/$output" "$output"

echo "Экспорт выполнен: $output"

nano "$output"
