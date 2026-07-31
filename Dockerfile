FROM python:3.12-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt


FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STATIC_ROOT=/app/staticfiles

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system app \
    && adduser --system --ingroup app --home /app app

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels /wheels/* && rm -rf /wheels

COPY --chown=app:app . .
COPY --chown=app:app docker/entrypoint.sh /entrypoint.sh
COPY --chown=app:app docker/start-web.sh /start-web.sh

RUN sed -i 's/\r$//' /entrypoint.sh /start-web.sh \
    && chmod +x /entrypoint.sh /start-web.sh \
    && mkdir -p /app/staticfiles \
    && chown -R app:app /app

USER app

# Validate the committed migration graph, prove that it applies to an empty
# database and build immutable production static assets into the image.
RUN set -eu; \
    export DJANGO_ENV=production \
    DEBUG=0 \
    SECRET_KEY=ci-build-only-secret-key-with-more-than-fifty-characters-2026 \
    ALLOWED_HOSTS=localhost \
    CSRF_TRUSTED_ORIGINS=https://localhost \
    DB_ENGINE=django.db.backends.sqlite3 \
    DB_NAME=/tmp/cadet-journal-build-check.sqlite3 \
    MIGRATION_MODE=check \
    STATIC_ROOT=/app/staticfiles; \
    python manage.py makemigrations --check --dry-run; \
    python manage.py migrate --noinput; \
    python manage.py collectstatic --noinput --clear; \
    rm -f /tmp/cadet-journal-build-check.sqlite3

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/start-web.sh"]
