#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Использование: $0 <github_repo_url> [target_dir]"
  echo "Пример: $0 git@github.com:your-org/cadet_journal.git /opt/cadet_journal"
  exit 1
fi

REPO_URL="$1"
TARGET_DIR="${2:-/opt/cadet_journal}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker не установлен. Сначала установите Docker и плагин Docker Compose."
  exit 1
fi

if [[ ! -d "$TARGET_DIR/.git" ]]; then
  sudo mkdir -p "$TARGET_DIR"
  sudo chown -R "$USER":"$USER" "$TARGET_DIR"
  git clone "$REPO_URL" "$TARGET_DIR"
else
  echo "Репозиторий уже существует в $TARGET_DIR, пропускаем клонирование."
fi

cd "$TARGET_DIR"

./scripts/ensure-env-files.sh

docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml up -d --build

docker update --restart unless-stopped cadet-journal-web-1 cadet-journal-db-1 || true

echo "Подготовка сервера завершена."
