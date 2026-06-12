#!/usr/bin/env bash
# Start script for Render: migrate, warm the topic index, serve.
set -e

python django/manage.py migrate --noinput

# Bootstrap the admin account from DJANGO_SUPERUSER_* env vars (idempotent:
# creates the account, or resets its password to the current env value).
# Render's free tier has no shell, so this replaces "manage.py createsuperuser".
# Delete the variables once you've logged in and set your own password.
python django/manage.py ensure_superuser || true

# Build the classifier topic index before workers accept traffic, so the
# first real query doesn't pay the embedding cost. Failure is non-fatal:
# the API falls back to trigger matching and logs a warning.
python django/manage.py build_topic_index || echo "Topic index build failed; API will use trigger fallback."

cd django
exec gunicorn core.wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-1}" \
  --threads 4 \
  --timeout 120
