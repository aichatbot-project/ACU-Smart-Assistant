#!/bin/sh
set -e
if [ "${RUN_MIGRATIONS:-}" = "1" ] || [ "${RUN_MIGRATIONS:-}" = "true" ]; then
  python manage.py migrate --noinput
fi
exec "$@"
