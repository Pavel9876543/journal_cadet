# Docker deployment and CI/CD

## What is implemented

- CI: lint + Django checks + tests + Docker build check
- CD: on `main` push builds Docker image, pushes to GHCR, deploys to server over SSH
- Production deploy uses prebuilt image (`ghcr.io/...:latest`) for fast updates
- Production `.env.prod` is generated from GitHub Secrets on every deploy
- Superuser is created/verified automatically during container start
- Containers auto-start after reboot via `restart: unless-stopped`

## 1) Required repository settings

Set in GitHub repository -> `Settings` -> `Secrets and variables`.

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

No manual `.env.prod` creation is required.
CD will generate it from secrets automatically.

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

## 5) Local development mode

```bash
cp .env.dev.example .env.dev
./scripts/run-local.sh
```

Open: `http://localhost:8000`
