#!/usr/bin/env bash
set -euo pipefail

: "${APP_DIR:?APP_DIR is required}"
: "${REPO_CLONE_URL:?REPO_CLONE_URL is required}"
: "${DEPLOY_SHA:?DEPLOY_SHA is required}"
: "${ENV_UPLOAD_PATH:?ENV_UPLOAD_PATH is required}"

if [[ ! "$APP_DIR" = /* ]]; then
  echo "APP_DIR должен быть абсолютным путём." >&2
  exit 2
fi
if [[ ! "$DEPLOY_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "DEPLOY_SHA должен быть полным SHA коммита." >&2
  exit 2
fi
if [[ ! -f "$ENV_UPLOAD_PATH" ]]; then
  echo "Загруженный env-файл не найден: $ENV_UPLOAD_PATH" >&2
  exit 2
fi

cleanup_upload() {
  rm -f "$ENV_UPLOAD_PATH"
}
trap cleanup_upload EXIT HUP INT TERM

CURRENT_USER="$(id -un)"

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=()
elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
  SUDO=(sudo -n)
else
  echo "Нужен root или sudo без интерактивного пароля." >&2
  exit 1
fi

install_host_dependencies() {
  local required=(ca-certificates curl gnupg git python3)
  local missing=()
  local command_name
  for command_name in curl gpg git python3; do
    command -v "$command_name" >/dev/null 2>&1 || missing+=("$command_name")
  done
  if ((${#missing[@]} == 0)); then
    return
  fi
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Автоустановка поддерживает Debian/Ubuntu с apt-get." >&2
    exit 1
  fi
  export DEBIAN_FRONTEND=noninteractive
  "${SUDO[@]}" apt-get update -y
  "${SUDO[@]}" apt-get install -y "${required[@]}"
}

install_docker_if_needed() {
  if command -v docker >/dev/null 2>&1 && (
    docker compose version >/dev/null 2>&1 ||
    "${SUDO[@]}" docker compose version >/dev/null 2>&1
  ); then
    return
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Docker не установлен, а apt-get недоступен." >&2
    exit 1
  fi

  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian) local docker_distro="$ID" ;;
    *)
      echo "Автоустановка Docker не поддерживает ${ID:-unknown}." >&2
      exit 1
      ;;
  esac

  local docker_codename="${VERSION_CODENAME:-}"
  if [[ -z "$docker_codename" ]]; then
    echo "Не удалось определить codename системы." >&2
    exit 1
  fi

  "${SUDO[@]}" install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$docker_distro/gpg" | \
    "${SUDO[@]}" gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
  "${SUDO[@]}" chmod a+r /etc/apt/keyrings/docker.gpg

  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$docker_distro $docker_codename stable" | \
    "${SUDO[@]}" tee /etc/apt/sources.list.d/docker.list >/dev/null

  export DEBIAN_FRONTEND=noninteractive
  "${SUDO[@]}" apt-get update -y
  "${SUDO[@]}" apt-get install -y \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin
  "${SUDO[@]}" systemctl enable --now docker
}

install_host_dependencies
install_docker_if_needed

if docker info >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  DOCKER_CMD=(docker)
else
  DOCKER_CMD=("${SUDO[@]}" docker)
fi

"${SUDO[@]}" mkdir -p "$APP_DIR"
if [[ ! -w "$APP_DIR" ]]; then
  "${SUDO[@]}" chown -R "$CURRENT_USER":"$CURRENT_USER" "$APP_DIR"
fi

if [[ ! -d "$APP_DIR/.git" ]]; then
  git clone "$REPO_CLONE_URL" "$APP_DIR"
fi

cd "$APP_DIR"
git config --global --add safe.directory "$APP_DIR"
git remote set-url origin "$REPO_CLONE_URL"
git fetch --prune origin main

if ! git cat-file -e "${DEPLOY_SHA}^{commit}" 2>/dev/null; then
  echo "Коммит $DEPLOY_SHA не найден после git fetch." >&2
  exit 1
fi
if [[ "$(git rev-parse origin/main)" != "$DEPLOY_SHA" ]]; then
  echo "Отказ: DEPLOY_SHA не совпадает с текущим origin/main." >&2
  exit 1
fi

git reset --hard "$DEPLOY_SHA"
git clean -fdx

umask 077
mv "$ENV_UPLOAD_PATH" .env.prod
trap - EXIT HUP INT TERM
chmod 600 .env.prod
chmod +x scripts/*.sh docker/*.sh

RELEASE_REVISION="$DEPLOY_SHA" ./scripts/run-prod.sh

"${DOCKER_CMD[@]}" image prune -f >/dev/null || true
printf 'Production deployed at %s\n' "$DEPLOY_SHA"
