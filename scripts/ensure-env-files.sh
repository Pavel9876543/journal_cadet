#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ $# -ne 1 ]]; then
  echo "Использование: $0 <.env.dev|.env.prod>"
  exit 1
fi

TARGET_ENV_FILE="$1"

case "$TARGET_ENV_FILE" in
  .env.dev) EXAMPLE_ENV_FILE=".env.dev.example" ;;
  .env.prod) EXAMPLE_ENV_FILE=".env.prod.example" ;;
  *)
    echo "Неподдерживаемый env-файл: $TARGET_ENV_FILE"
    echo "Допустимые значения: .env.dev или .env.prod"
    exit 1
    ;;
esac

ensure_from_example() {
  local target="$1"
  local example="$2"

  if [[ -f "$target" ]]; then
    echo "Используется существующий $target"
    return
  fi

  if [[ ! -f "$example" ]]; then
    echo "Отсутствует $example; не удалось создать $target"
    exit 1
  fi

  cp "$example" "$target"
  echo "Создан $target из $example"
}

ensure_from_example "$TARGET_ENV_FILE" "$EXAMPLE_ENV_FILE"
