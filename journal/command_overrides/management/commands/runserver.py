from __future__ import annotations

from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.contrib.staticfiles.management.commands.runserver import (
    Command as DjangoRunserverCommand,
)


def _disable_static_caching(response):
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


class NoCacheStaticFilesHandler(StaticFilesHandler):
    """Serve development assets without retaining stale browser copies."""

    def serve(self, request):
        return _disable_static_caching(super().serve(request))


class Command(DjangoRunserverCommand):
    def get_handler(self, *args, **options):
        handler = super().get_handler(*args, **options)
        if isinstance(handler, StaticFilesHandler):
            return NoCacheStaticFilesHandler(handler.application)
        return handler
