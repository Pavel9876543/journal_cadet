# Docker production и CI/CD

## Архитектура запуска

Production состоит из четырёх сервисов:

- `web` — Django/ASGI через Uvicorn;
- `db` — PostgreSQL 16;
- `redis` — непостоянный production-кэш;
- `backup` — ежедневный `pg_dump` в volume `pg_backups`.

`docker-compose.yml` содержит общую конфигурацию и преобразует
`POSTGRES_*` в `DB_*` для Django. `docker-compose.prod.yml` добавляет HTTPS-
настройки, Redis, backup, локальную публикацию порта и ограничения контейнеров.
Поэтому database credentials не дублируются в `.env.prod`.

Production image:

1. устанавливает закреплённые зависимости;
2. проверяет отсутствие незакоммиченных миграций;
3. применяет миграции к пустой временной SQLite-базе;
4. собирает static manifest;
5. запускается от системного пользователя `app`;
6. в Compose получает read-only filesystem, `cap_drop: ALL` и
   `no-new-privileges`.

Runtime entrypoint ждёт PostgreSQL, повторно проверяет migration graph,
выполняет `migrate` и `ensure_superuser`. `collectstatic` в runtime не
выполняется.

## Env-файлы

| Окружение | Файл | Хранение |
|---|---|---|
| Development Docker | `.env.dev` | локально, не коммитится |
| Tests/CI | `.env.test` | безопасные значения, коммитится |
| Production | `.env.prod` | локально/на сервере, не коммитится |

Примеры: `.env.dev.example`, `.env.test.example`, `.env.prod.example`.

```bash
./scripts/ensure-env-files.sh .env.dev
./scripts/ensure-env-files.sh .env.test
./scripts/ensure-env-files.sh .env.prod
```

Проверка:

```bash
python scripts/validate_env.py --file .env.test --environment test
python scripts/validate_env.py --file .env.prod --environment production
```

Production validator отклоняет:

- незаданные обязательные значения;
- placeholder-пароли и короткие секреты;
- HTTP origins в `CSRF_TRUSTED_ORIGINS`;
- схемы/пути в `ALLOWED_HOSTS`;
- лишние или повторяющиеся переменные;
- некорректные порты, timeout, Redis memory/policy и Uvicorn settings.

## Ручной production-запуск

```bash
cp .env.prod.example .env.prod
nano .env.prod
./scripts/run-prod.sh
```

Скрипт последовательно выполняет:

```text
validate .env.prod
compose config --quiet
compose pull db redis backup
compose build web
compose up -d --wait
Django check
Redis PING
```

При ошибке выводятся последние логи `web`, `db`, `redis`, `backup`.

Production web доступен только на `127.0.0.1:${APP_PORT}`. Настройте Nginx,
Caddy или другой reverse proxy с TLS и заголовками `Host`,
`X-Forwarded-Proto`, `X-Forwarded-For`.

## CI

`.github/workflows/ci.yml` запускается на:

- любом `push`;
- `pull_request`;
- `merge_group` для GitHub merge queue.

### Job `Django and tests`

- установка `requirements-dev.txt` и `pip check`;
- валидация production/test env;
- синтаксис всех shell scripts;
- Ruff;
- `makemigrations --check --dry-run`;
- `manage.py check`;
- `manage.py check --deploy --fail-level WARNING`;
- `collectstatic`;
- быстрые тесты без tag `slow`;
- медленные тесты с tag `slow`, последовательно.

Команды локально:

```bash
./scripts/test-fast.sh -v 2
./scripts/test-slow.sh -v 2
./scripts/test.sh -v 2
```

### Job `Production Docker smoke test`

- собирает image с Buildx;
- проверяет static manifest и executable entrypoint;
- проверяет image с read-only root filesystem;
- поднимает реальный production Compose с PostgreSQL, Redis, backup и web;
- ждёт healthchecks;
- обращается к `/health/`;
- проверяет Django и Redis.

### Job `Required CI result`

Это единая required check. Она успешна только если успешно завершились оба
предыдущих job.

## Почему CD нельзя запустить отдельно

`.github/workflows/cd.yml` имеет только trigger `workflow_call`. В нём нет:

- `workflow_dispatch`;
- `workflow_run`;
- tag/create trigger;
- прямого push trigger.

Его вызывает только `deploy-production` из CI с зависимостью `needs: ci-passed`
и только для `push` в `main`. Pull requests и merge queue не получают SSH и
production secrets. После merge GitHub создаёт push в `main`, и деплоится SHA,
который уже прошёл CI.

## GitHub Environment

Создайте `Settings → Environments → production`.

### Secrets

Обязательные:

- `SSH_HOST`;
- `SSH_USER`;
- `SSH_PRIVATE_KEY` — отдельный Ed25519 private key для входа на сервер;
- `SSH_KNOWN_HOSTS` — результат `ssh-keyscan -H SERVER_IP`;
- `DJANGO_SECRET_KEY`;
- `DJANGO_ALLOWED_HOSTS`;
- `DJANGO_CSRF_TRUSTED_ORIGINS`;
- `POSTGRES_DB`;
- `POSTGRES_USER`;
- `POSTGRES_PASSWORD`;
- `DJANGO_SUPERUSER_USERNAME`;
- `DJANGO_SUPERUSER_PASSWORD`.

Дополнительные:

- `SSH_PORT` — по умолчанию `22`;
- `DJANGO_SUPERUSER_EMAIL`;
- `DATA_TOOLS_PASSWORD`.

### Variables

Основные:

- `APP_DIR=/opt/cadet_journal`;
- `REPO_CLONE_URL=https://github.com/ORG/REPO.git` или SSH URL.

Настройка производительности (необязательно):

- `APP_PORT=8000`;
- `TRUSTED_PROXY_COUNT=1`;
- `DB_CONN_MAX_AGE=60`;
- `CACHE_DEFAULT_TIMEOUT=300`;
- `CACHE_KEY_PREFIX=cadet-journal`;
- `REDIS_MAXMEMORY=256mb`;
- `REDIS_MAXMEMORY_POLICY=allkeys-lru`;
- `WEB_CONCURRENCY=3`;
- `WEB_TIMEOUT_KEEP_ALIVE=5`;
- `WEB_LOG_LEVEL=info`;
- `WEB_ACCESS_LOG=1`;
- `DB_WAIT_TIMEOUT=60`;
- `DEPLOY_WAIT_TIMEOUT=240`;
- `BACKUP_INTERVAL_SECONDS=86400`;
- `BACKUP_RETENTION_DAYS=30`.

## SSH: два разных ключа

1. **GitHub Actions → server.** Public key размещается в
   `~deploy/.ssh/authorized_keys`, private key — в `SSH_PRIVATE_KEY`.
2. **Server → private GitHub repository.** Public key добавляется как repository
   Deploy key с read-only доступом; private key остаётся на сервере.

Пример настройки второго ключа под пользователем `deploy`:

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
ssh-keygen -t ed25519 -C cadet-journal-repository \
  -f ~/.ssh/id_ed25519_github
ssh-keyscan -H github.com >> ~/.ssh/known_hosts
chmod 600 ~/.ssh/known_hosts
cat ~/.ssh/id_ed25519_github.pub
```

Публичную часть добавьте в `Settings → Deploy keys → Add deploy key`, не включая
write access. В `~/.ssh/config`:

```text
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519_github
  IdentitiesOnly yes
```

Проверка: `ssh -T git@github.com`. Для публичного HTTPS clone URL второй ключ не
нужен.

`SSH_KNOWN_HOSTS` обязателен: CD использует `StrictHostKeyChecking=yes` и не
принимает неизвестный host key автоматически.

## Что делает CD на сервере

`scripts/remote-deploy.sh`:

1. проверяет `APP_DIR`, полный SHA и загруженный env;
2. при необходимости устанавливает Git/Python/Docker на Debian/Ubuntu;
3. клонирует либо обновляет repository;
4. делает `git fetch origin main`;
5. требует точного совпадения `DEPLOY_SHA == origin/main`;
6. выполняет `git reset --hard DEPLOY_SHA` и очищает рабочее дерево;
   более новый push отменяет предыдущий CD через concurrency;
7. атомарно помещает `.env.prod` с правами `600`;
8. запускает `run-prod.sh`;
9. очищает неиспользуемые Docker images.

Production env формируется на GitHub runner скриптом
`scripts/render_prod_env.py`, валидируется до передачи и копируется через SCP.
Секреты не передаются в аргументах SSH-команды.

## Branch protection / Ruleset

Для `main` включите:

1. Require a pull request before merging;
2. Require status checks to pass;
3. required check `CI / Required CI result`;
4. запрет force push и удаления branch;
5. при необходимости merge queue.

GitHub использует термин pull request. Merge request — название аналогичного
механизма в GitLab. Event `merge_group` обеспечивает проверки GitHub merge
queue.

## Первый деплой

1. Проверьте DNS и HTTPS reverse proxy.
2. Добавьте server user и SSH public key.
3. Добавьте GitHub Environment secrets/variables.
4. Для private repository настройте server Deploy key.
5. Защитите `main` required check-ом.
6. Сделайте merge PR либо push в `main`.
7. Откройте Actions → CI и проверьте jobs.
8. После `Required CI result` откроется вызываемый CD job.
9. На сервере проверьте:

```bash
cd /opt/cadet_journal
./scripts/run-prod.sh
docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml ps
docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml logs --tail 200 web
```

## Обновление и отказ

- Ошибка любого CI job: CD не вызывается.
- Ошибка SSH/env/git SHA: Docker stack не обновляется.
- Ошибка build/start/healthcheck: CD завершается с логами и красным статусом.
- Миграции должны быть backward-compatible: автоматический rollback к старому
  image после применённой schema migration намеренно не выполняется.

## Резервные копии

Сервис `backup` сразу создаёт custom-format dump, проверяет его через
`pg_restore --list`, затем повторяет операцию через
`BACKUP_INTERVAL_SECONDS`. Старые dumps удаляются по
`BACKUP_RETENTION_DAYS`.

```bash
docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml exec backup ls -lah /backups
```

Восстановление описано в `docs/backup-restore.md`.
