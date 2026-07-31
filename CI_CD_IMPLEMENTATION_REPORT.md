# Отчёт по production Docker и CI/CD

Дата проверки: 31 июля 2026 года.

## Реализовано

- Production Docker image собирает immutable static assets на этапе build.
- Runtime-контейнер `web` запускается с read-only root filesystem, без root,
  с `cap_drop: ALL` и `no-new-privileges`.
- Production Compose включает PostgreSQL, Redis-кэш и проверяемые резервные
  копии PostgreSQL.
- `.env.prod.example` содержит только изменяемые production-параметры;
  неизменяемые security/database mapping задаются Compose.
- `.env.test` и `.env.test.example` содержат одинаковые безопасные значения CI.
- Добавлен строгий валидатор production/test env и генератор `.env.prod` из
  GitHub Environment secrets/variables.
- CI запускается для `push`, `pull_request` и `merge_group`.
- Быстрые тесты и сценарии с tag `slow` разделены на реальные исполняемые
  скрипты.
- CI отдельно проверяет Python/Django и полный production Docker Compose stack.
- Production CD вызывается только reusable workflow после успешного общего job
  `Required CI result` и только для push в `main`.
- Удалены самостоятельные `workflow_dispatch`, `workflow_run`, tag/create
  способы запуска production CD.
- CD использует SSH private key, обязательный known_hosts и точный SHA текущего
  `origin/main`.
- README и `DEPLOY_DOCKER.md` содержат пошаговую настройку сервера, GitHub
  Environment, deploy keys и branch protection.

## Коммиты

1. `063b520` — production Docker и env-конфигурация.
2. `6e1c405` — профиль CI и разделение тестов.
3. `4aeb3fa` — production CD после успешного CI.
4. `1d8503b` — инструкции GitHub CD и Docker production.

## Выполненные проверки в доступном окружении

- AST/compile-проверка: 83 Python-файла.
- `bash -n`/`sh -n`: все shell-скрипты.
- `node --check`: 12 JavaScript-файлов.
- YAML-разбор: оба GitHub workflow и три Compose-файла.
- Строгая валидация `.env.prod.example`, `.env.test` и совпадения test example.
- Генерация production env с кавычками в пароле и проверка прав `0600`.
- Проверка инвариантов CI → CD и отсутствия обходных CD-триггеров.
- `git diff --check` и `git fsck --full --no-dangling`.

## Ограничение локальной проверки

В рабочем контейнере отсутствуют Django и Docker, а доступный package index не
предоставляет зависимости проекта. Поэтому реальные Django-тесты и Docker smoke
stack должны выполниться в GitHub Actions после push. Workflow теперь содержит
все эти проверки и не вызовет production CD при их ошибке.
