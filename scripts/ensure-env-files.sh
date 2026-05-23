#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

ensure_from_example() {
  local target="$1"
  local example="$2"

  if [[ -f "$target" ]]; then
    echo "Using existing $target"
    return
  fi

  if [[ ! -f "$example" ]]; then
    echo "Missing $example; cannot create $target"
    exit 1
  fi

  cp "$example" "$target"
  echo "Created $target from $example"
}

ensure_from_example ".env" ".env.example"
ensure_from_example ".env.dev" ".env.dev.example"
ensure_from_example ".env.prod" ".env.prod.example"
