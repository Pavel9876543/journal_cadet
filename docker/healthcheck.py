from __future__ import annotations

import os
from urllib.request import Request, urlopen


def _healthcheck_host() -> str:
    allowed_hosts = [
        value.strip()
        for value in os.getenv('ALLOWED_HOSTS', '').split(',')
        if value.strip()
    ]
    for host in allowed_hosts:
        if host == '*':
            return 'localhost'
        if host.startswith('.') and len(host) > 1:
            return f'health{host}'
        return host
    return 'localhost'


def main() -> None:
    request = Request(
        'http://127.0.0.1:8000/health/',
        headers={
            'Host': _healthcheck_host(),
            'X-Forwarded-Proto': 'https',
        },
    )
    with urlopen(request, timeout=5) as response:
        if response.status != 200:
            raise SystemExit(f'Unexpected healthcheck status: {response.status}')


if __name__ == '__main__':
    main()
