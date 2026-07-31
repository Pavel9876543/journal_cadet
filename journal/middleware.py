from __future__ import annotations

from django.conf import settings


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
        content_type = response.get('Content-Type', '').partition(';')[0].strip().lower()
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
