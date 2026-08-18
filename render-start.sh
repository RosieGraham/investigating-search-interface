#!/usr/bin/env bash
# Start script for Render: migrate, warm the topic index, serve.
# Index build failures are fatal: the process must not accept workshop traffic
# without a current index. Liveness (/healthz) is separate from readiness.
set -e

python django/manage.py migrate --noinput

# Bootstrap the admin account from DJANGO_SUPERUSER_* env vars (idempotent:
# creates the account, or resets its password to the current env value).
# Render's free tier has no shell, so this replaces "manage.py createsuperuser".
# Delete the variables once you've logged in and set your own password.
python django/manage.py ensure_superuser || true

python django/manage.py build_topic_index
python django/manage.py build_prompt_index

cd django
exec gunicorn core.wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-1}" \
  --threads 4 \
  --timeout 120
