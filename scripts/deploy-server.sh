#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -n "$(git status --short)" ]]; then
  echo "Отказ: рабочее дерево содержит незакоммиченные изменения." >&2
  exit 1
fi

cat <<'WARNING'
Этот скрипт не получает новый код и не заменяет GitHub CD.
Он только повторно собирает уже проверенный и находящийся на сервере commit.
Новые версии должны попадать на production через CI -> CD в GitHub Actions.
WARNING

RELEASE_REVISION="$(git rev-parse HEAD)" ./scripts/run-prod.sh
