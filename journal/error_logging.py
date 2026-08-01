from __future__ import annotations

import logging
import traceback
from typing import Any


def _exception_text(exception: BaseException | None = None, exc_info=None) -> str:
    if exc_info:
        return ''.join(traceback.format_exception(*exc_info)).strip()
    if exception is not None:
        return ''.join(
            traceback.format_exception(
                type(exception),
                exception,
                exception.__traceback__,
            )
        ).strip()
    return ''


def persist_error(
    *,
    request=None,
    message: str,
    exception: BaseException | None = None,
    exc_info=None,
    level: str = 'ERROR',
    logger_name: str = 'journal',
    status_code: int | None = None,
    metadata: dict[str, Any] | None = None,
    max_records: int = 1000,
    mark_request: bool = True,
) -> bool:
    """Persist one handled or unhandled application error safely.

    Logging must never hide the original problem, so all database/bootstrap
    failures are swallowed. ``mark_request`` prevents the same exception from
    being written again when Django later emits its ``django.request`` record.
    """
    try:
        from django.apps import apps

        if not apps.ready:
            return False

        from .models import ErrorLog

        request_id = getattr(request, 'request_id', '') if request is not None else ''
        user = getattr(request, 'user', None) if request is not None else None
        user_label = (
            str(user)[:150]
            if user is not None and getattr(user, 'is_authenticated', False)
            else ''
        )
        ErrorLog.objects.create(
            level=str(level or 'ERROR')[:20],
            logger_name=str(logger_name or 'journal')[:255],
            message=str(message or 'Неизвестная ошибка'),
            exception=_exception_text(exception, exc_info),
            request_id=str(request_id)[:64],
            status_code=status_code if isinstance(status_code, int) else None,
            method=str(getattr(request, 'method', ''))[:16],
            path=str(getattr(request, 'path', ''))[:512],
            user_label=user_label,
            metadata=dict(metadata or {}),
        )
        ErrorLog.prune_old_entries(max_records)
        if request is not None and mark_request:
            request._journal_error_logged = True
        return True
    except Exception:
        return False


def log_handled_error(
    request,
    exception: BaseException,
    *,
    status_code: int = 400,
    logger_name: str = 'journal.handled',
    metadata: dict[str, Any] | None = None,
) -> bool:
    return persist_error(
        request=request,
        message=str(exception) or exception.__class__.__name__,
        exception=exception,
        status_code=status_code,
        logger_name=logger_name,
        metadata={'handled': True, **(metadata or {})},
    )


class DatabaseErrorHandler(logging.Handler):
    """Persist ERROR/CRITICAL records and keep at most 1000 newest entries."""

    def __init__(self, max_records: int = 1000) -> None:
        super().__init__(level=logging.ERROR)
        self.max_records = max(1, min(int(max_records), 1000))

    def emit(self, record: logging.LogRecord) -> None:
        request = getattr(record, 'request', None)
        if request is not None and getattr(request, '_journal_error_logged', False):
            return

        status_code = getattr(record, 'status_code', None)
        if status_code is None:
            status_code = getattr(getattr(record, 'response', None), 'status_code', None)

        metadata: dict[str, Any] = {}
        release_revision = getattr(record, 'release_revision', '')
        if release_revision:
            metadata['release_revision'] = str(release_revision)[:128]

        persist_error(
            request=request,
            message=record.getMessage(),
            exc_info=record.exc_info,
            level=record.levelname,
            logger_name=record.name,
            status_code=status_code if isinstance(status_code, int) else None,
            metadata=metadata,
            max_records=self.max_records,
        )
