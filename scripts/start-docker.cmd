@echo off
setlocal
cd /d %~dp0\..

if not exist .env (
  copy .env.example .env >nul
  echo Created .env from template.
)

if not exist .env.dev (
  copy .env.dev.example .env.dev >nul
  echo Created .env.dev from template.
)

if not exist .env.prod (
  copy .env.prod.example .env.prod >nul
  echo Created .env.prod from template.
)

docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml up -d --build
if %errorlevel% neq 0 exit /b %errorlevel%

echo Local dev started at http://localhost:8000
