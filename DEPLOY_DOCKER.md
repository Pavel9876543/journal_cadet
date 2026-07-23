# Развертывание Docker и CI/CD

## Что реализовано

- CI: закреплённые зависимости, Ruff, проверки миграций и Django, тесты, production security check, сборка статики и проверка запуска Docker-образа.
- CD: при push в `main` обновляет код на сервере по SSH, атомарно формирует `.env.prod` и запускает production-стек.
- Production-образ собирается на сервере из текущего коммита; внешний container registry не требуется.
- Суперпользователь создаётся или проверяется автоматически при старте контейнера.
- Контейнеры автоматически поднимаются после перезагрузки через `restart: unless-stopped`.
- Опасные инструменты создания тестовых данных и очистки базы в production принудительно отключены.

## Поведение файлов окружения

Каждый Docker-скрипт использует только свой env-файл:

- локальный запуск — `.env.dev`;
- production-запуск — `.env.prod`;
- если файла нет, он создаётся из соответствующего `.example`;
- существующий файл не перезаписывается локальными скриптами;
- CD создаёт новый `.env.prod` во временном файле с правами текущего пользователя и затем атомарно заменяет рабочий файл.

Подготовка файлов вручную:

```bash
./scripts/ensure-env-files.sh .env.dev
./scripts/ensure-env-files.sh .env.prod
```

## Локальный запуск Docker

```bash
./scripts/run-local.sh
```

Скрипт подготовит `.env.dev`, соберёт образ и запустит development-стек. Приложение будет доступно по адресу `http://localhost:8000`.

Для Windows:

```bat
scripts\start-docker.cmd
```

## 1. Настройки GitHub Actions

Откройте `Settings` → `Secrets and variables` → `Actions`.

### Secrets

Инфраструктура:

- `SSH_HOST`;
- `SSH_USER`;
- `SSH_PASSWORD`;
- `SSH_PORT` — обычно `22`.

Приложение и база данных:

- `DJANGO_SECRET_KEY`;
- `DJANGO_ALLOWED_HOSTS` — например `example.com,www.example.com`;
- `DJANGO_CSRF_TRUSTED_ORIGINS` — например `https://example.com,https://www.example.com`;
- `POSTGRES_DB`;
- `POSTGRES_USER`;
- `POSTGRES_PASSWORD`;
- `DATA_TOOLS_PASSWORD` — может быть пустым, потому что опасные инструменты в production отключены.

Суперпользователь:

- `DJANGO_SUPERUSER_USERNAME`;
- `DJANGO_SUPERUSER_EMAIL`;
- `DJANGO_SUPERUSER_PASSWORD`;
- `DJANGO_SUPERUSER_ROTATE_PASSWORD` — `0` или `1`.

### Variables

- `REPO_CLONE_URL` — например `git@github.com:ORG/REPO.git`;
- `APP_DIR` — например `/opt/cadet_journal`.

## 2. Требования к серверу

CD поддерживает Debian и Ubuntu с `apt-get`. Пользователь должен быть `root` либо иметь `sudo` без интерактивного ввода пароля. Workflow при необходимости устанавливает Docker Engine и Docker Compose plugin.

Репозиторий должен быть доступен серверу по адресу из `REPO_CLONE_URL`. Для SSH-адреса добавьте серверный публичный ключ в deploy keys репозитория.

## 3. Первый деплой

1. Добавьте secrets и variables.
2. Проверьте SSH-доступ к серверу и доступ сервера к репозиторию.
3. Запустите workflow `CD` вручную через `workflow_dispatch` либо выполните push в `main`.

Workflow:

1. проверит обязательные переменные;
2. установит Docker при необходимости;
3. клонирует или обновит репозиторий;
4. сбросит серверную рабочую копию точно к `origin/main`;
5. атомарно создаст `.env.prod`;
6. соберёт и запустит production-стек;
7. применит миграции, проверит суперпользователя и соберёт статику через entrypoint.

## 4. Обычный деплой

1. Сделайте push в `main`.
2. Убедитесь, что CI завершился успешно.
3. CD автоматически развернёт этот коммит на сервере.

## 5. Ручной production-запуск

В уже клонированном репозитории:

```bash
./scripts/run-prod.sh
```

Для обновления текущей ветки и запуска:

```bash
./scripts/deploy-server.sh
```

Для первичного клонирования на сервер:

```bash
./scripts/bootstrap-server.sh git@github.com:ORG/REPO.git /opt/cadet_journal
```

Перед ручным запуском обязательно замените placeholder-значения в `.env.prod`; с тестовым `SECRET_KEY` Django намеренно не стартует.

## 6. Резервное копирование

Production Compose запускает отдельный сервис `backup`, который сохраняет архивы PostgreSQL в volume `pg_backups`. Инструкция по проверке и восстановлению: `docs/backup-restore.md`.
