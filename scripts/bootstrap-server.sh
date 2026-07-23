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

if ! docker compose version >/dev/null 2>&1; then
  echo "Плагин Docker Compose недоступен."
  exit 1
fi

if [ "$(id -u)" -eq 0 ]; then
  SUDO=()
elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
  SUDO=(sudo -n)
else
  echo "Ошибка: для подготовки $TARGET_DIR нужны права root или sudo без запроса пароля."
  exit 1
fi

if [[ ! -d "$TARGET_DIR/.git" ]]; then
  "${SUDO[@]}" mkdir -p "$TARGET_DIR"
  "${SUDO[@]}" chown -R "$USER":"$USER" "$TARGET_DIR"
  git clone "$REPO_URL" "$TARGET_DIR"
else
  echo "Репозиторий уже существует в $TARGET_DIR, пропускаем клонирование."
fi

cd "$TARGET_DIR"
./scripts/run-prod.sh

echo "Подготовка сервера завершена."
