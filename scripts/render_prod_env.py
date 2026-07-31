#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from validate_env import read_env_file, validate_production


ENV_MAPPING = {
    'SECRET_KEY': ('DJANGO_SECRET_KEY', None),
    'ALLOWED_HOSTS': ('DJANGO_ALLOWED_HOSTS', None),
    'CSRF_TRUSTED_ORIGINS': ('DJANGO_CSRF_TRUSTED_ORIGINS', None),
    'APP_PORT': ('APP_PORT', '8000'),
    'POSTGRES_DB': ('POSTGRES_DB', None),
    'POSTGRES_USER': ('POSTGRES_USER', None),
    'POSTGRES_PASSWORD': ('POSTGRES_PASSWORD', None),
    'DJANGO_SUPERUSER_USERNAME': ('DJANGO_SUPERUSER_USERNAME', None),
    'DJANGO_SUPERUSER_EMAIL': ('DJANGO_SUPERUSER_EMAIL', ''),
    'DJANGO_SUPERUSER_PASSWORD': ('DJANGO_SUPERUSER_PASSWORD', None),
    'DATA_TOOLS_PASSWORD': ('DATA_TOOLS_PASSWORD', ''),
    'TRUSTED_PROXY_COUNT': ('TRUSTED_PROXY_COUNT', '1'),
    'DB_CONN_MAX_AGE': ('DB_CONN_MAX_AGE', '60'),
    'CACHE_DEFAULT_TIMEOUT': ('CACHE_DEFAULT_TIMEOUT', '300'),
    'CACHE_KEY_PREFIX': ('CACHE_KEY_PREFIX', 'cadet-journal'),
    'REDIS_MAXMEMORY': ('REDIS_MAXMEMORY', '256mb'),
    'REDIS_MAXMEMORY_POLICY': ('REDIS_MAXMEMORY_POLICY', 'allkeys-lru'),
    'WEB_CONCURRENCY': ('WEB_CONCURRENCY', '3'),
    'WEB_TIMEOUT_KEEP_ALIVE': ('WEB_TIMEOUT_KEEP_ALIVE', '5'),
    'WEB_LOG_LEVEL': ('WEB_LOG_LEVEL', 'info'),
    'WEB_ACCESS_LOG': ('WEB_ACCESS_LOG', '1'),
    'DB_WAIT_TIMEOUT': ('DB_WAIT_TIMEOUT', '60'),
    'DEPLOY_WAIT_TIMEOUT': ('DEPLOY_WAIT_TIMEOUT', '240'),
    'BACKUP_INTERVAL_SECONDS': ('BACKUP_INTERVAL_SECONDS', '86400'),
    'BACKUP_RETENTION_DAYS': ('BACKUP_RETENTION_DAYS', '30'),
}


def _dotenv_quote(value: str) -> str:
    if '\n' in value or '\r' in value:
        raise ValueError('dotenv values must not contain line breaks')
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"


def main() -> int:
    parser = argparse.ArgumentParser(description='Render .env.prod from CI variables.')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()

    values: dict[str, str] = {}
    missing: list[str] = []
    for target_key, (source_key, default) in ENV_MAPPING.items():
        value = os.getenv(source_key)
        if value is None:
            value = default
        if value is None:
            missing.append(source_key)
            continue
        values[target_key] = value

    if missing:
        raise SystemExit(f'Не заданы обязательные CI secrets: {", ".join(missing)}')

    args.output.parent.mkdir(parents=True, exist_ok=True)
    content = ''.join(
        f'{key}={_dotenv_quote(value)}\n'
        for key, value in values.items()
    )
    args.output.write_text(content, encoding='utf-8')
    args.output.chmod(0o600)

    parsed = read_env_file(args.output)
    errors = validate_production(parsed)
    if errors:
        args.output.unlink(missing_ok=True)
        raise SystemExit('\n'.join(f'- {error}' for error in errors))

    print(f'Production env rendered: {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
