#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Использование: $0 <github_repo_url> [target_dir]"
  echo "Пример: $0 git@github.com:your-org/cadet_journal.git /opt/cadet_journal"
  exit 1
fi

REPO_URL="$1"
TARGET_DIR="${2:-/opt/cadet_journal}"
CURRENT_USER="$(id -un)"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker не установлен. Сначала установите Docker Engine и Compose plugin."
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin недоступен."
  exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=()
elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
  SUDO=(sudo -n)
else
  echo "Для подготовки $TARGET_DIR нужны root или sudo без запроса пароля."
  exit 1
fi

if [[ ! -d "$TARGET_DIR/.git" ]]; then
  "${SUDO[@]}" mkdir -p "$TARGET_DIR"
  "${SUDO[@]}" chown -R "$CURRENT_USER":"$CURRENT_USER" "$TARGET_DIR"
  git clone "$REPO_URL" "$TARGET_DIR"
else
  echo "Репозиторий уже существует в $TARGET_DIR."
fi

cd "$TARGET_DIR"
./scripts/ensure-env-files.sh .env.prod

cat <<MESSAGE
Серверная директория подготовлена: $TARGET_DIR

Дальше:
1. Заполните $TARGET_DIR/.env.prod реальными значениями.
2. Настройте HTTPS reverse proxy на 127.0.0.1:8000.
3. Выполните: cd $TARGET_DIR && ./scripts/run-prod.sh

Для штатного CD настройте GitHub Environment production по README.
MESSAGE
