#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

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

ensure_from_example ".env" ".env.example"
ensure_from_example ".env.dev" ".env.dev.example"
ensure_from_example ".env.prod" ".env.prod.example"
