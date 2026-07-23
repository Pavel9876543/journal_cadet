#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_CMD="${PYTHON:-python}"

run_seed_commands() {
  "$PYTHON_CMD" manage.py migrate --noinput
  "$PYTHON_CMD" manage.py seed_data
}

# Внутри контейнера или в локальном virtualenv выполняем команды напрямую.
if [ -f /.dockerenv ] || "$PYTHON_CMD" -c 'import django' >/dev/null 2>&1; then
  echo "=== Применение миграций ==="
  run_seed_commands
  echo "=== Тестовые данные созданы ==="
  exit 0
fi

if [ ! -f ".env.dev" ]; then
  echo "Ошибка: файл .env.dev не найден в корне проекта."
  echo "Создайте его командой: ./scripts/ensure-env-files.sh .env.dev"
  exit 1
fi

if docker info >/dev/null 2>&1; then
  DOCKER_CMD=(docker)
elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
  DOCKER_CMD=(sudo -n docker)
else
  echo "Ошибка: не найдено рабочее окружение Django и недоступен Docker."
  exit 1
fi

COMPOSE_CMD=(
  "${DOCKER_CMD[@]}"
  compose
  --env-file .env.dev
  -f docker-compose.yml
  -f docker-compose.dev.yml
)

echo "=== Проверка контейнеров ==="
"${COMPOSE_CMD[@]}" ps

echo "=== Применение миграций ==="
"${COMPOSE_CMD[@]}" exec -T web python manage.py migrate --noinput

echo "=== Заполнение тестовыми данными ==="
"${COMPOSE_CMD[@]}" exec -T web python manage.py seed_data

echo "=== Тестовые данные созданы ==="
