# Развертывание Docker и CI/CD

## Что реализовано

- CI: линтинг + проверки Django + тесты + проверка сборки Docker-образа
- CD: при push в `main` обновляет код на сервере по SSH и запускает production-стек
- Для production образ собирается на сервере из текущего кода
- Суперпользователь создаётся/проверяется автоматически при старте контейнера
- Контейнеры автоматически поднимаются после перезагрузки через `restart: unless-stopped`

## Поведение файлов окружения

Каждый Docker-скрипт запуска готовит только свой env-файл:

- Локальный запуск использует только `.env.dev` (если файла нет, создаётся из `.env.dev.example`)
- Продакшен-запуск использует только `.env.prod` (если файла нет, создаётся из `.env.prod.example`)
- Если целевой файл уже существует, он не перезаписывается

Это означает, что перед запуском через скрипты не нужно вручную копировать env-файлы.

## Локальный запуск Docker (GitHub не нужен)

Для локальной разработки доступ к GitHub не требуется.

```bash
./scripts/run-local.sh
```

Скрипт автоматически подготовит env-файлы и запустит:

```bash
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Откройте: `http://localhost:8000`

Для Windows:

```bat
scripts\start-docker.cmd
```

## 1) Обязательные настройки репозитория (GitHub Actions)

Задаются в репозитории GitHub: `Settings` -> `Secrets and variables`.

Эти настройки нужны для CI/CD и автодеплоя на сервер, но не для локального запуска Docker.

### Секреты (`Actions secrets`)

Инфраструктура:
- `SSH_HOST`
- `SSH_USER`
- `SSH_PASSWORD`
- `SSH_PORT` (обычно `22`)
- `GHCR_PULL_USER` (опционально, если пакет образа публичный)
- `GHCR_PULL_TOKEN` (опционально, если пакет образа публичный)

Приложение и база данных:
- `DJANGO_SECRET_KEY`
- `DJANGO_ALLOWED_HOSTS` (пример: `example.com,www.example.com`)
- `DJANGO_CSRF_TRUSTED_ORIGINS` (пример: `https://example.com,https://www.example.com`)
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `DATA_TOOLS_PASSWORD`

Суперпользователь:
- `DJANGO_SUPERUSER_USERNAME`
- `DJANGO_SUPERUSER_EMAIL`
- `DJANGO_SUPERUSER_PASSWORD`
- `DJANGO_SUPERUSER_ROTATE_PASSWORD` (`0` или `1`)

GitHub Secrets с префиксом `DJANGO_` используются только в workflow. При деплое они записываются в `.env.prod` как `SECRET_KEY`, `ALLOWED_HOSTS` и `CSRF_TRUSTED_ORIGINS`, которые читает Django-приложение.

### Переменные (`Actions variables`)

- `REPO_CLONE_URL` (пример: `git@github.com:ORG/REPO.git`)
- `APP_DIR` (пример: `/opt/cadet_journal`)

## 2) Одноразовая подготовка сервера

```bash
sudo systemctl enable docker
sudo systemctl start docker
```

## 3) Первый деплой / деплой на новый сервер

1. Один раз добавьте все секреты и переменные в настройках GitHub-репозитория.
2. Убедитесь, что на сервере есть SSH-доступ и установлен Docker.
3. Запустите GitHub Action `CD` (вручную через `workflow_dispatch`) или выполните push в `main`.

Во время деплоя workflow выполнит:
- клонирование репозитория, если его ещё нет на сервере
- генерацию `.env.prod` из секретов
- сборку свежего Docker-образа на сервере
- запуск `docker compose up -d`
- применение миграций и проверку/создание суперпользователя при старте контейнера

## 4) Ежедневный быстрый процесс деплоя

1. Сделать push коммита в `main`
2. Дождаться прохождения CI
3. CD автоматически соберёт образ и выполнит деплой

## 5) Ручной запуск production из репозитория на сервере

Если запускаете production вручную из клонированного репозитория:

```bash
./scripts/run-prod.sh
```

Этот скрипт также автоматически создаёт `.env.prod` из `.env.prod.example`, если `.env.prod` отсутствует.
