from __future__ import annotations

import logging
import traceback
from typing import Any


class DatabaseErrorHandler(logging.Handler):
    """Persist ERROR/CRITICAL records and keep at most 1000 newest entries.

    The model import is intentionally lazy: logging is configured while Django's
    app registry and database may still be unavailable. Failures in the logging
    backend are swallowed so an original application error is never replaced by
    a secondary logging error.
    """

    def __init__(self, max_records: int = 1000) -> None:
        super().__init__(level=logging.ERROR)
        self.max_records = max(1, min(int(max_records), 1000))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from django.apps import apps

            if not apps.ready:
                return

            from .models import ErrorLog

            request = getattr(record, 'request', None)
            request_id = getattr(record, 'request_id', '') or getattr(
                request,
                'request_id',
                '',
            )
            status_code = getattr(record, 'status_code', None)
            if status_code is None:
                status_code = getattr(getattr(record, 'response', None), 'status_code', None)

            exception_text = ''
            if record.exc_info:
                exception_text = ''.join(traceback.format_exception(*record.exc_info)).strip()
            elif getattr(record, 'exc_text', None):
                exception_text = str(record.exc_text)

            user = getattr(request, 'user', None)
            if user is not None and getattr(user, 'is_authenticated', False):
                user_label = str(user)[:150]
            else:
                user_label = ''

            metadata: dict[str, Any] = {}
            release_revision = getattr(record, 'release_revision', '')
            if release_revision:
                metadata['release_revision'] = str(release_revision)[:128]

            ErrorLog.objects.create(
                level=record.levelname[:20],
                logger_name=record.name[:255],
                message=record.getMessage(),
                exception=exception_text,
                request_id=str(request_id)[:64],
                status_code=status_code if isinstance(status_code, int) else None,
                method=str(getattr(request, 'method', ''))[:16],
                path=str(getattr(request, 'path', ''))[:512],
                user_label=user_label,
                metadata=metadata,
            )
            ErrorLog.prune_old_entries(self.max_records)
        except Exception:
            # Never trigger recursive logging or hide the original exception.
            return
