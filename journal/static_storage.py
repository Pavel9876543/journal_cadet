from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlencode

from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import StaticFilesStorage

try:
    from whitenoise.storage import CompressedStaticFilesStorage
except ImportError:  # Local Python may intentionally omit production WhiteNoise.
    CompressedStaticFilesStorage = StaticFilesStorage


class DevelopmentStaticFilesStorage(CompressedStaticFilesStorage):
    """Give locally served CSS/JavaScript a content-versioned URL."""

    def url(self, name):
        url = super().url(name)
        clean_name = name.split('?', 1)[0].split('#', 1)[0]
        if not clean_name.lower().endswith(('.css', '.js')):
            return url

        source_path = finders.find(clean_name)
        if isinstance(source_path, (list, tuple)):
            source_path = source_path[0] if source_path else None
        if not source_path:
            return url

        try:
            digest = hashlib.sha256(Path(source_path).read_bytes()).hexdigest()[:12]
        except OSError:
            return url

        separator = '&' if '?' in url else '?'
        return f'{url}{separator}{urlencode({"v": digest})}'
