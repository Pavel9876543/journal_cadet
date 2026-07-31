#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

./scripts/ensure-env-files.sh .env.prod
python3 ./scripts/validate_env.py --file .env.prod --environment production

if docker info >/dev/null 2>&1; then
  DOCKER_CMD=(docker)
elif command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
  DOCKER_CMD=(sudo -n docker)
else
  echo "Ошибка: Docker недоступен для текущего пользователя." >&2
  exit 1
fi

if ! "${DOCKER_CMD[@]}" compose version >/dev/null 2>&1; then
  echo "Ошибка: Docker Compose plugin не установлен." >&2
  exit 1
fi

if [[ -z "${RELEASE_REVISION:-}" ]]; then
  if git rev-parse --verify HEAD >/dev/null 2>&1; then
    RELEASE_REVISION="$(git rev-parse --short=12 HEAD)"
  else
    RELEASE_REVISION="manual"
  fi
fi
export RELEASE_REVISION

DEPLOY_WAIT_TIMEOUT="${DEPLOY_WAIT_TIMEOUT:-$(
  python3 - <<'PY'
from pathlib import Path
from scripts.validate_env import read_env_file
print(read_env_file(Path('.env.prod')).get('DEPLOY_WAIT_TIMEOUT', '240'))
PY
)}"
if [[ ! "$DEPLOY_WAIT_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; then
  echo "DEPLOY_WAIT_TIMEOUT должен быть положительным целым числом." >&2
  exit 2
fi

COMPOSE=(
  "${DOCKER_CMD[@]}" compose
  --env-file .env.prod
  -f docker-compose.yml
  -f docker-compose.prod.yml
)

show_failure_logs() {
  local status=$?
  trap - ERR
  echo "Production deploy failed. Current container state:" >&2
  "${COMPOSE[@]}" ps >&2 || true
  "${COMPOSE[@]}" logs --tail 200 web db redis backup >&2 || true
  exit "$status"
}
trap show_failure_logs ERR

"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" pull db redis backup
"${COMPOSE[@]}" build --pull web
"${COMPOSE[@]}" up \
  -d \
  --remove-orphans \
  --wait \
  --wait-timeout "$DEPLOY_WAIT_TIMEOUT"

"${COMPOSE[@]}" exec -T web python manage.py check
"${COMPOSE[@]}" exec -T redis redis-cli ping | grep -qx PONG
"${COMPOSE[@]}" ps

trap - ERR
printf 'Production stack is healthy. Release: %s\n' "$RELEASE_REVISION"
