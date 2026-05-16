@echo off
setlocal
cd /d %~dp0\..

if not exist .env.dev (
  copy .env.dev.example .env.dev >nul
  echo Created .env.dev from template.
)

docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml up -d --build
if %errorlevel% neq 0 exit /b %errorlevel%

echo Local dev started at http://localhost:8000
