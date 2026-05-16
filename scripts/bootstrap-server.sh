#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <github_repo_url> [target_dir]"
  echo "Example: $0 git@github.com:your-org/cadet_journal.git /opt/cadet_journal"
  exit 1
fi

REPO_URL="$1"
TARGET_DIR="${2:-/opt/cadet_journal}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install Docker and Docker Compose plugin first."
  exit 1
fi

if [[ ! -d "$TARGET_DIR/.git" ]]; then
  sudo mkdir -p "$TARGET_DIR"
  sudo chown -R "$USER":"$USER" "$TARGET_DIR"
  git clone "$REPO_URL" "$TARGET_DIR"
else
  echo "Repo already exists in $TARGET_DIR, skipping clone."
fi

cd "$TARGET_DIR"

if [[ ! -f .env.prod ]]; then
  cp .env.prod.example .env.prod
  echo "Created .env.prod. Edit it now: $TARGET_DIR/.env.prod"
fi

docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml up -d --build

docker update --restart unless-stopped cadet-journal-web-1 cadet-journal-db-1 || true

echo "Server bootstrap complete."
