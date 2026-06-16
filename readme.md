# Cadet Journal

Django-приложение для ведения журнала кадет/учеников: группы, предметы, преподаватели, оценки, итоговые оценки, заявки на курсы и временные учетные данные.

## Требования

- Python 3.12+
- Docker и Docker Compose, если используется запуск через контейнеры
- PostgreSQL для Docker-запуска
- SQLite или PostgreSQL для локального запуска без Docker

## Файлы окружения

Проект читает переменные окружения из `.env.dev` и `.env.prod`, если файлы существуют. Уже заданные переменные окружения имеют приоритет над значениями из файлов.

Для создания env-файла из примера:

```bash
./scripts/ensure-env-files.sh .env.dev
./scripts/ensure-env-files.sh .env.prod
```

Основные переменные:

- `DEBUG` - `1` для разработки, `0` для production.
- `SECRET_KEY` - секретный ключ Django.
- `ALLOWED_HOSTS` - хосты через запятую.
- `CSRF_TRUSTED_ORIGINS` - доверенные origins через запятую, например `https://example.com`.
- `DB_ENGINE` - движок БД, например `django.db.backends.postgresql` или `django.db.backends.sqlite3`.
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` - настройки подключения Django к БД.
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` - настройки контейнера PostgreSQL.
- `WAIT_FOR_DB=1` - ждать доступности PostgreSQL при старте контейнера.
- `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL`, `DJANGO_SUPERUSER_PASSWORD` - данные суперпользователя.
- `DJANGO_SUPERUSER_ROTATE_PASSWORD=1` - принудительно обновлять пароль существующего суперпользователя.

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

Перед запуском настройте `.env.prod`: поменяйте `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, пароли БД и данные суперпользователя.

```bash
./scripts/run-prod.sh
```

Скрипт создаст `.env.prod` из `.env.prod.example`, если файла нет, и выполнит:

```bash
docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml up -d --build --remove-orphans
```

При старте контейнера `docker/entrypoint.sh` автоматически выполняет:

```bash
python manage.py migrate --noinput
python manage.py ensure_superuser
python manage.py collectstatic --noinput
```

## Тестовое заполнение БД

Полное тестовое заполнение:

```bash
./scripts/seed_all.sh
```

Скрипт выполняет:

```bash
python manage.py migrate
python manage.py seed_data
```

Важно: `python manage.py seed_data` очищает существующие заявки, оценки, итоги, учеников, преподавателей, группы, предметы, временные учетные данные и всех пользователей. Используйте эту команду только для тестовой БД или когда перезаполнение допустимо.

Можно запускать команды отдельно:

```bash
python manage.py seed_data
python manage.py create_teacher_accounts
python manage.py create_student_accounts
python manage.py ensure_superuser
```

Для Docker-запуска добавляйте `exec web`:

```bash
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml exec web ./scripts/seed_all.sh
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml exec web python manage.py seed_data
```

После `seed_data` создается тестовый администратор:

```text
login: admin
password: см. secrets.csv
```

Также создаются тестовые преподаватели и ученики с временными паролями. Все тестовые логины и пароли сохраняются в `secrets.csv` в корне проекта.

## Экспорт временных учетных данных в CSV

### Таблица `TemporaryCredential`

Эта таблица содержит временные логины и пароли, созданные командами `create_teacher_accounts`, `create_student_accounts` и тестовым наполнением.

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
login,temporary_password,created_at
```

### Таблица `TemporaryStudentCredential`

Эта таблица содержит временные учетные данные учеников, созданные при регистрации заявки на курсы, включая телефон ученика.

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

Для Docker-запуска:

```bash
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml exec web python manage.py export_temporary_credentials --output exports/temporary_credentials.csv
docker compose --env-file .env.dev -f docker-compose.yml -f docker-compose.dev.yml exec web python manage.py export_student_credentials_with_phone --output exports/students.csv
```

## XLSX-экспорт из админки

Для учетных данных учеников доступна выгрузка:

```text
/admin/student-credentials/export.xlsx
```

Доступ разрешен только сотруднику (`is_staff=True`). Файл содержит логин, временный пароль и телефон ученика.

## Тесты и проверки

Запуск тестов:

```bash
python manage.py test
```

Django system check:

```bash
python manage.py check
```

Линтинг в CI выполняется через `ruff`:

```bash
pip install ruff
ruff check .
```

## Деплой

Подробные инструкции по Docker/CD находятся в `DEPLOY_DOCKER.md`.

Для ручного деплоя из уже склонированного репозитория на сервере:

```bash
./scripts/deploy-server.sh
```

Скрипт делает `git pull --ff-only` текущей ветки и запускает `./scripts/run-prod.sh`.

Для первичной подготовки сервера:

```bash
./scripts/bootstrap-server.sh <github_repo_url> [target_dir]
```

Пример:

```bash
./scripts/bootstrap-server.sh git@github.com:your-org/cadet_journal.git /opt/cadet_journal
```

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
python manage.py collectstatic --noinput
```
