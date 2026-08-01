# Cadet Journal

Django-приложение для ведения журнала кадет/учеников: группы, предметы, преподаватели, оценки, итоговые оценки, заявки на курсы и временные учетные данные.

## Требования

- Python 3.12+
- Docker и Docker Compose, если используется запуск через контейнеры
- PostgreSQL для Docker-запуска
- SQLite или PostgreSQL для локального запуска без Docker

## Файлы окружения

Проект сначала читает общий `.env`, затем файл выбранного окружения. Явные
переменные процесса имеют наивысший приоритет.

- `DJANGO_ENV=development` — `.env.dev`;
- `DJANGO_ENV=test` — `.env.test`;
- `DJANGO_ENV=production` — `.env.prod`;
- `DJANGO_ENV_FILE` позволяет явно указать другой файл.

Подготовка файлов из примеров:

```bash
./scripts/ensure-env-files.sh .env.dev
./scripts/ensure-env-files.sh .env.test
./scripts/ensure-env-files.sh .env.prod
```

`.env.test` содержит только безопасные тестовые значения и хранится в Git.
Настоящий `.env.prod` не коммитится: при CD он формируется из GitHub Secrets,
проверяется и передаётся на сервер с правами `600`.

Основные production-переменные:

- `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`;
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`;
- `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL`,
  `DJANGO_SUPERUSER_PASSWORD`;
- `APP_PORT` — локальный порт reverse proxy, по умолчанию `8000`;
- `TRUSTED_PROXY_COUNT` — число доверенных proxy-hop;
- `DB_CONN_MAX_AGE`, `CACHE_DEFAULT_TIMEOUT`, `CACHE_KEY_PREFIX`;
- `REDIS_MAXMEMORY`, `REDIS_MAXMEMORY_POLICY`;
- `WEB_CONCURRENCY`, `WEB_TIMEOUT_KEEP_ALIVE`, `WEB_LOG_LEVEL`,
  `WEB_ACCESS_LOG`;
- `DB_WAIT_TIMEOUT`, `DEPLOY_WAIT_TIMEOUT`;
- `BACKUP_INTERVAL_SECONDS`, `BACKUP_RETENTION_DAYS`;
- `DATA_TOOLS_PASSWORD` — отдельный пароль сервисных инструментов.

Фиксированные безопасные параметры (`DJANGO_ENV`, HTTPS cookies, HSTS,
`MIGRATION_MODE=check`, адреса `db` и `redis`, отключение опасных инструментов)
задаются в `docker-compose.prod.yml`, поэтому не дублируются в `.env.prod`.

Проверка env-файлов:

```bash
python scripts/validate_env.py --file .env.test --environment test
python scripts/validate_env.py --file .env.prod --environment production
```

## Локальный запуск через Docker

```bash
./scripts/run-local.sh
```

Скрипт создаст `.env.dev` из `.env.dev.example`, если файла нет, соберет образ и запустит стек:

```bash
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Приложение будет доступно по адресу:

```text
http://localhost:8000
```

Для Windows:

```bat
scripts\start-docker.cmd
```

Полезные Docker-команды:

```bash
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml logs -f web
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml down
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml exec web python manage.py migrate
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml exec web python manage.py test
```

## Локальный запуск без Docker

Создайте виртуальное окружение и установите зависимости:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Для разработки и линтинга установите также закреплённые dev-зависимости:

```bash
pip install -r requirements-dev.txt
```

Если нужен SQLite, можно не задавать `DB_ENGINE`: по умолчанию используется `django.db.backends.sqlite3`, а файл БД создается как `db.sqlite3`.

Примените миграции и запустите сервер:

```bash
python manage.py migrate
python manage.py runserver
```

Админка доступна по адресу:

```text
http://127.0.0.1:8000/admin/
```

## Production-запуск вручную

Создайте `.env.prod` из примера и замените все значения `replace-with-*`:

```bash
./scripts/ensure-env-files.sh .env.prod
nano .env.prod
./scripts/run-prod.sh
```

`run-prod.sh`:

1. проверяет `.env.prod` и отклоняет placeholder, слабые или лишние значения;
2. проверяет итоговую Compose-конфигурацию;
3. обновляет образы PostgreSQL/Redis, собирает web-образ;
4. запускает стек и ждёт успешных healthcheck;
5. выполняет Django system check и проверяет Redis.

Приложение публикуется только на `127.0.0.1:${APP_PORT}:8000`. Перед ним должен
работать HTTPS reverse proxy (Nginx/Caddy), передающий `Host`,
`X-Forwarded-Proto` и корректный `X-Forwarded-For`.

Production-статика собирается во время Docker build. Контейнер `web` запускается
с read-only filesystem, сброшенными Linux capabilities и
`no-new-privileges`. При старте выполняются только проверка миграций,
`migrate` и `ensure_superuser`; код и статика на сервере не изменяются.

Полезные команды:

```bash
docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml ps
docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml logs -f web
docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml exec web python manage.py check
```

## Тестовое заполнение БД

Полное тестовое заполнение:

```bash
./scripts/seed_all.sh
```

Команда создаёт два соседних непересекающихся демонстрационных периода длительностью по 14 дней: архивный и активный. В каждом основном классе создаётся не более трёх учеников, причём одни и те же карточки учеников зачисляются в оба периода для проверки истории. Оба периода содержат группы, назначения, оценки, итоги и данные сдачи произведений; дополнительно создаются инструменты, партии оркестра, заявки и временные учётные данные.

Скрипт сначала применяет миграции, затем запускает `seed_data`. Он работает:

- напрямую, если запущен внутри контейнера или активного Python-окружения с Django;
- через development Docker Compose, если запускается на хосте без установленного Django.

Выполняемые Django-команды:

```bash
python manage.py migrate --noinput
python manage.py seed_data
```

Важно: `python manage.py seed_data` очищает существующие учебные данные и обычные аккаунты учеников/преподавателей, созданные для демо-набора. Суперпользователи, staff-пользователи и их пароли сохраняются. Используйте команду только для тестовой БД или когда перезаполнение допустимо.

Можно запускать команды отдельно:

```bash
python manage.py seed_data
python manage.py create_teacher_accounts
python manage.py create_student_accounts
python manage.py ensure_superuser
```

### Когда создаются временные учетные данные

Временный пароль записывается только одновременно с созданием нового аккаунта. Поддерживаемые пути создания:

- пользователь, ученик или преподаватель, созданный через админку;
- подтверждённая новая заявка на курсы;
- `createsuperuser` и первый запуск `ensure_superuser`;
- `create_teacher_accounts` и `create_student_accounts` для профилей без аккаунта;
- новые демонстрационные аккаунты из `seed_data`.

Редактирование ФИО, контактов, группы, ролей, активности, email или логина не меняет хэш пароля и не создаёт новый временный пароль. Повторный запуск команд также не сбрасывает пароли существующих аккаунтов. Низкоуровневое создание пользователя напрямую через ORM (`User.objects.create_user`) не сохраняет открытый пароль в таблицу: для терминала используйте предусмотренные management-команды.

Для Docker можно использовать как общий скрипт на хосте, так и запуск внутри контейнера:

```bash
./scripts/seed_all.sh
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml exec web ./scripts/seed_all.sh
```

После `seed_data` создается тестовый администратор:

```text
login: admin
password: см. secrets.csv
```

Также создаются временные пароли для всех пользователей: администраторов, преподавателей и учеников. Все тестовые логины и пароли сохраняются в `secrets.csv` в корне проекта.

## Экспорт временных учетных данных в CSV

### Таблица `TemporaryCredential`

Эта таблица содержит все временные учетные данные: логин, временный пароль, дату и время создания, а также номер телефона ученика, если учетная запись была создана через заявку на курсы.

Вывести CSV в терминал:

```bash
python manage.py export_temporary_credentials
```

Сохранить CSV в файл:

```bash
python manage.py export_temporary_credentials --output exports/temporary_credentials.csv
```

Колонки CSV:

```text
login,temporary_password,created_at,student_phone
```

Для выгрузки только учетных данных учеников с телефоном:

Сохранить CSV в файл по умолчанию:

```bash
python manage.py export_student_credentials_with_phone
```

Если `--output` не указан, файл будет создан в текущем каталоге с именем вида `YYYY_MM_students.csv`.

Сохранить в конкретный файл:

```bash
python manage.py export_student_credentials_with_phone --output exports/students.csv
```

Колонки CSV:

```text
login,temporary_password,student_phone
```

Команды экспорта не удаляют записи из базы.

Для production-контейнера доступен вспомогательный скрипт. Необязательный аргумент задаёт путь итогового файла:

```bash
./scripts/exp_tc.sh
./scripts/exp_tc.sh exports/students.csv
```

Для Docker-запуска:

```bash
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml exec web python manage.py export_temporary_credentials --output exports/temporary_credentials.csv
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml exec web python manage.py export_student_credentials_with_phone --output exports/students.csv
```

## XLSX-экспорт из админки

Полная выгрузка выбранного в админке учебного года доступна в разделе
«Инструменты данных». Она включает пользователей, учеников и преподавателей,
назначения, оценки и итоги, заявки, аттестацию по произведениям и временные
доступы. Отдельными листами выгружаются партии оркестра, каталог произведений,
квалификации преподавателей, доступы к учебному году, настройки регистрации и
контакты восстановления. Для архивного года персональные данные учеников берутся из сохранённого
снимка этого года, поэтому последующие изменения карточки не искажают архив.

В лист временных доступов входят ученики, преподаватели и, для активного года,
администраторы. Вместе с логином и паролем выгружаются ФИО, роль, телефон, дата
выдачи и учебный год.

Для учетных данных учеников доступна выгрузка:

```text
/admin/student-credentials/export.xlsx
```

Доступ разрешён только суперпользователю. Файл содержит логин, временный пароль, телефон ученика и связанную заявку, если она есть. Экспорт принимает только GET-запросы.

## Тесты и проверки

CI использует PostgreSQL и `.env.test`. Быстрый набор исключает тесты с тегом
`slow`, а конкурентные сценарии и полное заполнение `seed_data` выполняются
отдельно и последовательно:

```bash
./scripts/test-fast.sh -v 2
./scripts/test-slow.sh -v 2
```

Полный локальный набор:

```bash
./scripts/test.sh -v 2
```

Проверки перед push:

```bash
python scripts/validate_env.py --file .env.test --environment test
ruff check .
python manage.py makemigrations --check --dry-run
python manage.py check
./scripts/test-fast.sh
./scripts/test-slow.sh
```

## CD через GitHub Actions: пошаговый деплой

Production-деплой является вызываемым workflow и не может стартовать напрямую.
Он запускается только job-ом `deploy-production` после успешного
`Required CI result` для push в `main`. Pull request и merge queue получают тот
же CI, но не получают серверные секреты и не меняют production. После merge
создаётся push в `main`, который и запускает деплой проверенного SHA.

### 1. Подготовьте сервер

Используйте Debian/Ubuntu. Создайте пользователя для деплоя, разрешите ему
`sudo` без интерактивного пароля либо используйте `root`. Настройте DNS и HTTPS
reverse proxy на `http://127.0.0.1:8000`. Docker при отсутствии установит
CD-скрипт, однако для первого запуска лучше заранее проверить:

```bash
docker --version
docker compose version
git --version
```

Для приватного репозитория создайте **отдельный** ключ доступа сервера к GitHub:

```bash
sudo -u deploy mkdir -p /home/deploy/.ssh
sudo -u deploy chmod 700 /home/deploy/.ssh
sudo -u deploy ssh-keygen -t ed25519 -C cadet-journal-repository \
  -f /home/deploy/.ssh/id_ed25519_github
sudo -u deploy ssh-keyscan -H github.com \
  >> /home/deploy/.ssh/known_hosts
sudo -u deploy chmod 600 /home/deploy/.ssh/known_hosts
```

Добавьте содержимое `id_ed25519_github.pub` в GitHub:
`Settings → Deploy keys → Add deploy key`, оставив доступ только на чтение.
На сервере добавьте SSH-конфигурацию:

```text
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519_github
  IdentitiesOnly yes
```

После этого проверьте `ssh -T git@github.com` и задайте
`REPO_CLONE_URL=git@github.com:ORG/REPO.git`. Для публичного репозитория можно
оставить HTTPS URL и второй ключ не нужен.

### 2. Создайте SSH-ключ GitHub Actions → сервер

На доверенном компьютере:

```bash
ssh-keygen -t ed25519 -C cadet-journal-cd -f cadet_journal_cd
ssh-copy-id -i cadet_journal_cd.pub deploy@SERVER_IP
ssh-keyscan -H SERVER_IP
```

Закрытый ключ целиком сохраните в secret `SSH_PRIVATE_KEY`, а строку
`ssh-keyscan` — в `SSH_KNOWN_HOSTS`. Это не ключ доступа сервера к GitHub; для
приватного репозитория нужен отдельный Deploy key из предыдущего шага.

### 3. Создайте GitHub Environment `production`

Откройте `Settings → Environments → New environment → production`. При
необходимости включите required reviewers, чтобы успешный CI ожидал ручного
разрешения перед production.

Добавьте Environment secrets:

- `SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`, `SSH_KNOWN_HOSTS`;
- `SSH_PORT` — необязательно, по умолчанию `22`;
- `DJANGO_SECRET_KEY` — случайная строка не короче 50 символов;
- `DJANGO_ALLOWED_HOSTS`;
- `DJANGO_CSRF_TRUSTED_ORIGINS` — origins с `https://`;
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`;
- `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL`,
  `DJANGO_SUPERUSER_PASSWORD`;
- `DATA_TOOLS_PASSWORD` — необязательно, но рекомендуется.

Добавьте Environment variables:

- `APP_DIR=/opt/cadet_journal`;
- `REPO_CLONE_URL=https://github.com/ORG/REPO.git` для публичного репозитория
  или `git@github.com:ORG/REPO.git` для приватного;
- необязательно: `APP_PORT`, `TRUSTED_PROXY_COUNT`, `WEB_CONCURRENCY`,
  `DB_CONN_MAX_AGE`, параметры Redis и резервного копирования. Значения по
  умолчанию совпадают с `.env.prod.example`.

### 4. Защитите ветку `main`

В `Settings → Rules → Rulesets` (либо Branch protection rule):

1. запретите force push и удаление `main`;
2. включите обязательный pull request перед merge;
3. включите required status checks;
4. выберите проверку `CI / Required CI result`;
5. при использовании merge queue оставьте событие `merge_group` включённым —
   оно уже настроено в `.github/workflows/ci.yml`.

В GitHub термин используется `pull request`; это эквивалент merge request.

### 5. Выполните первый и последующие деплои

```bash
git push origin main
```

Последовательность:

1. CI запускает Ruff, проверки env/shell, миграции, Django checks, быстрые и
   медленные тесты;
2. отдельно собирается production Docker image и поднимается полный
   PostgreSQL + Redis + web smoke-стек;
3. job `Required CI result` завершается успешно только при успехе обоих job;
4. CD подключается к серверу по проверенному SSH host key;
5. runner формирует и валидирует `.env.prod`, сервер сверяет SHA с текущим
   `origin/main`, затем собирает и запускает production Compose;
6. при любой ошибке CI или healthcheck production-деплой считается неуспешным.

Прямого `workflow_dispatch`, tag-deploy и `workflow_run` больше нет: они не могут
обойти CI. Подробности и диагностика находятся в `DEPLOY_DOCKER.md`.

## Частые команды Django

Создать миграции после изменения моделей:

```bash
python manage.py makemigrations
```

Применить миграции:

```bash
python manage.py migrate
```

Создать суперпользователя вручную:

```bash
python manage.py createsuperuser
```

Создать или проверить суперпользователя из env-переменных:

```bash
python manage.py ensure_superuser
```

Собрать static-файлы:

```bash
python manage.py collectstatic --noinput --clear
```

## Обработка и журналирование ошибок

В production пользователю показываются безопасные страницы ошибок 400, 403, 404,
405, 408, 409, 413, 429, 500, 502, 503 и 504 с понятным описанием, действиями
для восстановления и уникальным кодом запроса. Для JSON/AJAX-запросов
возвращается единая структура `error` с тем же кодом. Техническая трассировка
пользователю не раскрывается.

Необработанные исключения, ответы HTTP со статусом ошибки и обработанные ошибки
форм журнала сохраняются в таблице «Журнал ошибок». В ней фиксируются код
ошибки, HTTP-статус, путь, метод, пользователь, источник, признак обработанной
ошибки, отдельное понятное сообщение для пользователя и трассировка. После появления 1001-й записи самые старые записи
автоматически удаляются: в базе всегда остаётся не более 1000 последних ошибок.

Переменные окружения:

```dotenv
ERROR_LOGGING_ENABLED=1
ERROR_LOG_MAX_RECORDS=1000
DJANGO_LOG_LEVEL=INFO
TIME_ZONE=Europe/Moscow
```


`TIME_ZONE` задаётся в формате IANA. Для московского времени используйте
`Europe/Moscow`. Значения `Moscow`, `UTC+3`, `UTC+03:00` и `+03:00` также
нормализуются в `Europe/Moscow`. Если переменная отсутствует, приложение
использует московское время UTC+3.

`ERROR_LOG_MAX_RECORDS` не может быть больше `1000`. В development и production
хранение ошибок включено по умолчанию. В тестовом окружении оно отключено для
стандартного logging handler, однако middleware по-прежнему проверяется
профильными тестами напрямую.
После добавления функции примените миграцию:

```bash
python manage.py migrate
```
