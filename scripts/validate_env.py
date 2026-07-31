#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


PRODUCTION_KEYS = {
    'SECRET_KEY',
    'ALLOWED_HOSTS',
    'CSRF_TRUSTED_ORIGINS',
    'APP_PORT',
    'POSTGRES_DB',
    'POSTGRES_USER',
    'POSTGRES_PASSWORD',
    'DJANGO_SUPERUSER_USERNAME',
    'DJANGO_SUPERUSER_EMAIL',
    'DJANGO_SUPERUSER_PASSWORD',
    'DATA_TOOLS_PASSWORD',
    'TRUSTED_PROXY_COUNT',
    'DB_CONN_MAX_AGE',
    'CACHE_DEFAULT_TIMEOUT',
    'CACHE_KEY_PREFIX',
    'REDIS_MAXMEMORY',
    'REDIS_MAXMEMORY_POLICY',
    'WEB_CONCURRENCY',
    'WEB_TIMEOUT_KEEP_ALIVE',
    'WEB_LOG_LEVEL',
    'WEB_ACCESS_LOG',
    'DB_WAIT_TIMEOUT',
    'DEPLOY_WAIT_TIMEOUT',
    'BACKUP_INTERVAL_SECONDS',
    'BACKUP_RETENTION_DAYS',
}

TEST_KEYS = {
    'DJANGO_ENV',
    'DEBUG',
    'SECRET_KEY',
    'ALLOWED_HOSTS',
    'CSRF_TRUSTED_ORIGINS',
    'DB_ENGINE',
    'DB_NAME',
    'DB_USER',
    'DB_PASSWORD',
    'DB_HOST',
    'DB_PORT',
    'DB_CONN_MAX_AGE',
}

PLACEHOLDER_MARKERS = (
    'change-this',
    'change-me',
    'replace-with',
    'example-secret',
    'your-secret',
)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        quote = value[0]
        value = value[1:-1]
        if quote == "'":
            return value.replace("\\'", "'").replace('\\\\', '\\')
        return bytes(value, 'utf-8').decode('unicode_escape')
    return value


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            raise ValueError(f'{path}:{line_number}: отсутствует знак =')
        key, raw_value = line.split('=', 1)
        key = key.strip()
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
            raise ValueError(f'{path}:{line_number}: некорректное имя переменной {key!r}')
        if key in values:
            raise ValueError(f'{path}:{line_number}: переменная {key} указана повторно')
        values[key] = _unquote(raw_value.strip())
    return values


def _require(values: dict[str, str], keys: set[str], errors: list[str]) -> None:
    for key in sorted(keys):
        if not values.get(key, '').strip():
            errors.append(f'обязательная переменная {key} не задана')


def _positive_int(values: dict[str, str], key: str, errors: list[str]) -> None:
    value = values.get(key)
    if value is None:
        return
    try:
        number = int(value)
    except ValueError:
        errors.append(f'{key} должен быть целым числом')
        return
    if number < 1:
        errors.append(f'{key} должен быть больше нуля')


def _nonnegative_int(values: dict[str, str], key: str, errors: list[str]) -> None:
    value = values.get(key)
    if value is None:
        return
    try:
        number = int(value)
    except ValueError:
        errors.append(f'{key} должен быть целым числом')
        return
    if number < 0:
        errors.append(f'{key} не может быть отрицательным')


def validate_production(
    values: dict[str, str],
    *,
    allow_placeholders: bool = False,
) -> list[str]:
    errors: list[str] = []
    unknown = sorted(set(values) - PRODUCTION_KEYS)
    if unknown:
        errors.append(f'лишние переменные production env: {", ".join(unknown)}')

    _require(
        values,
        {
            'SECRET_KEY',
            'ALLOWED_HOSTS',
            'CSRF_TRUSTED_ORIGINS',
            'POSTGRES_DB',
            'POSTGRES_USER',
            'POSTGRES_PASSWORD',
            'DJANGO_SUPERUSER_USERNAME',
            'DJANGO_SUPERUSER_PASSWORD',
        },
        errors,
    )

    if not allow_placeholders:
        for key in (
            'SECRET_KEY',
            'POSTGRES_PASSWORD',
            'DJANGO_SUPERUSER_PASSWORD',
            'DATA_TOOLS_PASSWORD',
        ):
            value = values.get(key, '').lower()
            if value and any(marker in value for marker in PLACEHOLDER_MARKERS):
                errors.append(f'{key} всё ещё содержит пример/placeholder')

    if len(values.get('SECRET_KEY', '')) < 50:
        errors.append('SECRET_KEY должен содержать не менее 50 символов')
    if len(values.get('POSTGRES_PASSWORD', '')) < 12:
        errors.append('POSTGRES_PASSWORD должен содержать не менее 12 символов')
    if len(values.get('DJANGO_SUPERUSER_PASSWORD', '')) < 12:
        errors.append('DJANGO_SUPERUSER_PASSWORD должен содержать не менее 12 символов')

    data_tools_password = values.get('DATA_TOOLS_PASSWORD', '')
    if data_tools_password and len(data_tools_password) < 12:
        errors.append('DATA_TOOLS_PASSWORD должен содержать не менее 12 символов')

    for host in values.get('ALLOWED_HOSTS', '').split(','):
        host = host.strip()
        if host and ('://' in host or '/' in host):
            errors.append(f'ALLOWED_HOSTS содержит не имя хоста: {host}')

    for origin in values.get('CSRF_TRUSTED_ORIGINS', '').split(','):
        origin = origin.strip()
        if origin and not origin.startswith('https://'):
            errors.append(f'production CSRF origin должен использовать HTTPS: {origin}')

    for key in (
        'APP_PORT',
        'TRUSTED_PROXY_COUNT',
        'CACHE_DEFAULT_TIMEOUT',
        'WEB_CONCURRENCY',
        'WEB_TIMEOUT_KEEP_ALIVE',
        'DB_WAIT_TIMEOUT',
        'DEPLOY_WAIT_TIMEOUT',
        'BACKUP_INTERVAL_SECONDS',
    ):
        _positive_int(values, key, errors)

    for key in ('DB_CONN_MAX_AGE', 'BACKUP_RETENTION_DAYS'):
        _nonnegative_int(values, key, errors)

    app_port = values.get('APP_PORT')
    if app_port and app_port.isdigit() and int(app_port) > 65535:
        errors.append('APP_PORT не может быть больше 65535')

    memory = values.get('REDIS_MAXMEMORY', '')
    if memory and not re.fullmatch(r'[1-9][0-9]*(kb|mb|gb)', memory.lower()):
        errors.append('REDIS_MAXMEMORY должен иметь вид 256mb, 1gb и т.п.')

    policy = values.get('REDIS_MAXMEMORY_POLICY', 'allkeys-lru')
    if policy not in {
        'allkeys-lru',
        'allkeys-lfu',
        'allkeys-random',
        'volatile-lru',
        'volatile-lfu',
        'volatile-random',
        'volatile-ttl',
        'noeviction',
    }:
        errors.append('REDIS_MAXMEMORY_POLICY содержит неподдерживаемое значение')

    if values.get('WEB_LOG_LEVEL', 'info') not in {
        'critical',
        'error',
        'warning',
        'info',
        'debug',
        'trace',
    }:
        errors.append('WEB_LOG_LEVEL содержит неподдерживаемое значение')

    if values.get('WEB_ACCESS_LOG', '1').lower() not in {
        '0',
        '1',
        'true',
        'false',
        'yes',
        'no',
        'on',
        'off',
    }:
        errors.append('WEB_ACCESS_LOG должен быть логическим значением')

    return errors


def validate_test(values: dict[str, str]) -> list[str]:
    errors: list[str] = []
    unknown = sorted(set(values) - TEST_KEYS)
    if unknown:
        errors.append(f'лишние переменные test env: {", ".join(unknown)}')

    _require(
        values,
        {
            'DJANGO_ENV',
            'DEBUG',
            'SECRET_KEY',
            'ALLOWED_HOSTS',
            'DB_ENGINE',
            'DB_NAME',
            'DB_USER',
            'DB_PASSWORD',
            'DB_HOST',
            'DB_PORT',
        },
        errors,
    )
    if values.get('DJANGO_ENV') not in {'test', 'testing'}:
        errors.append('DJANGO_ENV в тестовом env должен быть test или testing')
    if values.get('DEBUG') not in {'0', 'false', 'False'}:
        errors.append('DEBUG в тестовом env должен быть выключен')
    if values.get('DB_ENGINE') != 'django.db.backends.postgresql':
        errors.append('CI-тесты должны использовать PostgreSQL')
    _positive_int(values, 'DB_PORT', errors)
    _nonnegative_int(values, 'DB_CONN_MAX_AGE', errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description='Validate project dotenv files.')
    parser.add_argument('--file', required=True, type=Path)
    parser.add_argument('--environment', required=True, choices=('production', 'test'))
    parser.add_argument('--allow-placeholders', action='store_true')
    args = parser.parse_args()

    try:
        values = read_env_file(args.file)
    except (OSError, ValueError) as exc:
        print(f'Ошибка env-файла: {exc}', file=sys.stderr)
        return 2

    if args.environment == 'production':
        errors = validate_production(
            values,
            allow_placeholders=args.allow_placeholders,
        )
    else:
        errors = validate_test(values)

    if errors:
        for error in errors:
            print(f'- {error}', file=sys.stderr)
        return 2

    print(f'Env-файл {args.file} корректен для {args.environment}.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
