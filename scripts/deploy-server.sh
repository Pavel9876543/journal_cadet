#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

git fetch origin
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
git checkout "${CURRENT_BRANCH}"
git pull --ff-only origin "${CURRENT_BRANCH}"

./scripts/run-prod.sh

echo "Deploy complete on branch ${CURRENT_BRANCH}."
