# Docker deployment and CI/CD

## What is implemented

- CI: lint + Django checks + tests + Docker build check
- CD: on `main` push builds Docker image, pushes to GHCR, deploys to server over SSH
- Production deploy uses prebuilt image (`ghcr.io/...:latest`) for fast updates
- Superuser is created/verified automatically during container start
- Containers auto-start after reboot via `restart: unless-stopped`

## Environment files behavior

All Docker start scripts now use `scripts/ensure-env-files.sh` (or equivalent logic on Windows):

- If `.env` is missing -> create from `.env.example`
- If `.env.dev` is missing -> create from `.env.dev.example`
- If `.env.prod` is missing -> create from `.env.prod.example`
- If file already exists -> keep existing file unchanged

This means manual copying of env files is not required before script-based startup.

## Local Docker run (no GitHub required)

For local development, GitHub access is not required.

```bash
./scripts/run-local.sh
```

The script prepares env files automatically and starts:

```bash
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Open: `http://localhost:8000`

Windows:

```bat
scripts\start-docker.cmd
```

## 1) Required repository settings (GitHub Actions)

Set in GitHub repository -> `Settings` -> `Secrets and variables`.

These settings are required for CI/CD and server auto-deploy flows, not for local Docker run.

### Secrets (`Actions secrets`)

Infrastructure:
- `SSH_HOST`
- `SSH_USER`
- `SSH_PRIVATE_KEY`
- `SSH_PORT` (usually `22`)
- `GHCR_PULL_USER` (optional if image package is public)
- `GHCR_PULL_TOKEN` (optional if image package is public)

Application and database:
- `DJANGO_SECRET_KEY`
- `DJANGO_ALLOWED_HOSTS` (example: `example.com,www.example.com`)
- `DJANGO_CSRF_TRUSTED_ORIGINS` (example: `https://example.com,https://www.example.com`)
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`

Superuser:
- `DJANGO_SUPERUSER_USERNAME`
- `DJANGO_SUPERUSER_EMAIL`
- `DJANGO_SUPERUSER_PASSWORD`
- `DJANGO_SUPERUSER_ROTATE_PASSWORD` (`0` or `1`)

### Variables (`Actions variables`)

- `REPO_URL` (example `git@github.com:ORG/REPO.git`)
- `APP_DIR` (example `/opt/cadet_journal`)

## 2) One-time server preparation

```bash
sudo systemctl enable docker
sudo systemctl start docker
```

## 3) First deploy / new server deploy

1. Add all secrets and variables once in GitHub repo settings.
2. Ensure server has SSH access and Docker installed.
3. Run GitHub Action `CD` (manual `workflow_dispatch`) or push to `main`.

During deploy, workflow will:
- clone repo if not present
- generate `.env.prod` from secrets
- pull newest image from GHCR
- run `docker compose up -d`
- run migrations and ensure superuser at container startup

## 4) Daily fast deploy flow

1. Push commit to `main`
2. CI checks
3. CD builds image and deploys automatically

## 5) Manual production run from server repo

If you run production manually from the checked-out repo:

```bash
./scripts/run-prod.sh
```

This script also auto-creates missing `.env*` files from `*.example` before starting compose with `.env.prod`.
