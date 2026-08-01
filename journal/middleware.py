from __future__ import annotations

from django.conf import settings
from django.core.exceptions import PermissionDenied, SuspiciousOperation, ValidationError
from django.http import Http404
from django.utils.deprecation import MiddlewareMixin

from .error_logging import persist_error


class NoCacheDevelopmentStaticMiddleware:
    """Disable local static caching when assets are served outside runserver."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        static_url = getattr(settings, 'STATIC_URL', '/static/')
        if settings.DEBUG and request.path.startswith(static_url):
            response['Cache-Control'] = (
                'no-store, no-cache, must-revalidate, max-age=0'
            )
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'
        return response


class NoCacheHtmlMiddleware:
    """Prevent browsers and reverse proxies from retaining stale asset links."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        content_type = (
            response.get('Content-Type', '')
            .partition(';')[0]
            .strip()
            .lower()
        )
        if content_type == 'text/html':
            response['Cache-Control'] = (
                'private, no-store, no-cache, must-revalidate, max-age=0'
            )
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'
            release_revision = getattr(settings, 'RELEASE_REVISION', '')
            if release_revision:
                response['X-Release-Revision'] = release_revision
        return response


class RequestIdMiddleware:
    """Attach a safe request/error reference to every response and log record."""

    _ALLOWED = frozenset(
        'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.'
    )

    def __init__(self, get_response):
        self.get_response = get_response

    @classmethod
    def _incoming_request_id(cls, request) -> str:
        value = request.headers.get('X-Request-ID', '').strip()
        if (
            8 <= len(value) <= 64
            and all(character in cls._ALLOWED for character in value)
        ):
            return value
        return ''

    def __call__(self, request):
        from uuid import uuid4

        request.request_id = self._incoming_request_id(request) or uuid4().hex[:16]
        response = self.get_response(request)
        response['X-Request-ID'] = request.request_id
        return response


class ErrorLoggingMiddleware(MiddlewareMixin):
    """Persist both unhandled exceptions and handled HTTP error responses."""

    @staticmethod
    def _status_for_exception(exception: BaseException) -> int:
        if isinstance(exception, Http404):
            return 404
        if isinstance(exception, PermissionDenied):
            return 403
        if isinstance(exception, (SuspiciousOperation, ValidationError)):
            return 400
        return 500

    def process_exception(self, request, exception):
        persist_error(
            request=request,
            message=str(exception) or exception.__class__.__name__,
            exception=exception,
            status_code=self._status_for_exception(exception),
            logger_name='journal.request.exception',
            metadata={
                'handled': False,
                'exception_type': exception.__class__.__name__,
            },
            max_records=getattr(settings, 'ERROR_LOG_MAX_RECORDS', 1000),
        )
        return None

    def process_response(self, request, response):
        if (
            response.status_code >= 400
            and not getattr(request, '_journal_error_logged', False)
        ):
            persist_error(
                request=request,
                message=(
                    getattr(response, 'reason_phrase', '')
                    or f'HTTP {response.status_code}'
                ),
                status_code=response.status_code,
                logger_name='journal.request.response',
                metadata={'handled': True},
                max_records=getattr(settings, 'ERROR_LOG_MAX_RECORDS', 1000),
            )
        return response


class UserFriendlyErrorResponseMiddleware:
    """Replace bare infrastructure/client error pages with actionable responses.

    Django's dedicated 400/403/404/500 handlers remain the primary route. This
    middleware covers status codes such as 405, 408, 409, 413, 429 and gateway
    errors that may be returned directly by a view or upstream integration.
    JSON responses and already rendered HTML forms are preserved.
    """

    handled_statuses = frozenset({401, 405, 408, 409, 413, 415, 422, 429, 502, 503, 504})

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code not in self.handled_statuses:
            return response

        content_type = response.get('Content-Type', '').partition(';')[0].strip().lower()
        if content_type == 'application/json':
            return response
        if getattr(response, 'context_data', None):
            return response

        from .error_views import render_error_response

        return render_error_response(
            request,
            response.status_code,
            retry_url=request.get_full_path() if response.status_code >= 429 else None,
        )
