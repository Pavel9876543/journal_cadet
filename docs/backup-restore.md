# Резервное копирование PostgreSQL

В production Compose-сервис `backup` раз в сутки создает дамп PostgreSQL в
именованном томе `pg_backups`. Дамп создается сначала во временный файл и только
после успешного `pg_dump` проверяется через `pg_restore --list` и только затем
атомарно получает расширение `.dump`. Незавершённые временные файлы удаляются.

Срок хранения задается переменной `BACKUP_RETENTION_DAYS` (по умолчанию 30 дней).
Том с резервными копиями должен дополнительно копироваться на другой сервер или
в объектное хранилище. Резервная копия на том же сервере не защищает от потери
самого сервера.

Проверка списка копий:

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  exec backup ls -lh /backups
```

Разовый запуск:

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  exec backup /usr/local/bin/backup-postgres
```

## Восстановление

1. Остановить `web`, чтобы приложение не меняло данные во время восстановления.
2. Выбрать проверенный файл `.dump`.
3. Запустить скрипт восстановления. Он предварительно проверит формат архива и
   выполнит восстановление в одной транзакции с остановкой при первой ошибке.
4. Запустить `web`, применить миграции и проверить `/health/` и вход в журнал.

```sh
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop web
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm \
  -v ./docker/restore-postgres.sh:/usr/local/bin/restore-postgres:ro \
  backup /usr/local/bin/restore-postgres /backups/journal_db_YYYYMMDDTHHMMSSZ.dump
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d web
```

Восстановление необходимо регулярно проверять на отдельной тестовой базе.


Скрипт восстановления использует `--clean --if-exists --single-transaction`.
Если PostgreSQL сообщает об ошибке, изменения откатываются целиком. Перед
восстановлением всё равно необходимо сделать отдельную актуальную резервную
копию и проверить процедуру на тестовой базе.
