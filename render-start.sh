#!/usr/bin/env bash
# Start script for Render: migrate, warm the topic index, serve.
set -e

python django/manage.py migrate --noinput

# First-boot convenience: create the admin user from env vars if provided.
# (Render's free tier has no shell, so this replaces "manage.py createsuperuser".
# Set DJANGO_SUPERUSER_USERNAME / _EMAIL / _PASSWORD in the dashboard for the
# first deploy; remove them once you've logged in and changed the password.)
if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ]; then
  python django/manage.py createsuperuser --noinput || true
fi

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
