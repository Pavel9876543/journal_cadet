#!/usr/bin/env sh
set -eu

require_positive_integer() {
    value="$1"
    variable_name="$2"
    case "$value" in
        ''|*[!0-9]*)
            echo "$variable_name must be a positive integer, got: $value" >&2
            exit 2
            ;;
    esac
    if [ "$value" -lt 1 ]; then
        echo "$variable_name must be greater than zero." >&2
        exit 2
    fi
}

workers="${WEB_CONCURRENCY:-3}"
keep_alive="${WEB_TIMEOUT_KEEP_ALIVE:-5}"
log_level="${WEB_LOG_LEVEL:-info}"
access_log="${WEB_ACCESS_LOG:-1}"

require_positive_integer "$workers" WEB_CONCURRENCY
require_positive_integer "$keep_alive" WEB_TIMEOUT_KEEP_ALIVE

case "$log_level" in
    critical|error|warning|info|debug|trace) ;;
    *)
        echo "WEB_LOG_LEVEL must be one of: critical, error, warning, info, debug, trace." >&2
        exit 2
        ;;
esac

set -- uvicorn config.asgi:application \
    --host 0.0.0.0 \
    --port 8000 \
    --workers "$workers" \
    --timeout-keep-alive "$keep_alive" \
    --log-level "$log_level"

case "$access_log" in
    1|true|yes|on) ;;
    0|false|no|off) set -- "$@" --no-access-log ;;
    *)
        echo "WEB_ACCESS_LOG must be a boolean value (0/1, true/false)." >&2
        exit 2
        ;;
esac

exec "$@"
