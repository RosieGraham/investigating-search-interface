"""
Create or update the admin account from DJANGO_SUPERUSER_* environment variables.

Unlike Django's "createsuperuser --noinput", this is idempotent: if the
account already exists, its password is RESET to the current env value. That
means a mistyped first attempt is fixed by correcting the variable in the
Render dashboard and redeploying, with no shell access needed.

Runs at every boot (see render-start.sh) and does nothing when the variables
are absent, so the variables should be DELETED once you have logged in and
set a password of your own.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Create or update the superuser from DJANGO_SUPERUSER_* environment variables.'

    def handle(self, *args, **options):
        username = os.environ.get('DJANGO_SUPERUSER_USERNAME', '').strip()
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', '')
        email = os.environ.get('DJANGO_SUPERUSER_EMAIL', '').strip()

        if not username or not password:
            self.stdout.write('DJANGO_SUPERUSER_* not set; skipping admin account bootstrap.')
            return

        User = get_user_model()
        # IMPORTANT QUIRK: this project's User.save() (account/models.py)
        # forces username = lowercased email whenever an email is set, so
        # users log in with their EMAIL ADDRESS. Look up by email first,
        # then username, case-insensitively, to stay idempotent.
        user = None
        if email:
            user = User.objects.filter(email__iexact=email).first()
        if user is None:
            user = User.objects.filter(username__iexact=username).first()
        created = False
        if user is None:
            user = User(username=username)
            created = True

        if email:
            user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.is_active = True
        user.set_password(password)
        user.save()

        self.stdout.write(self.style.SUCCESS(
            'Admin account {}. Log in with username: {}'.format(
                'created' if created else 'updated (password reset)', user.username
            )
        ))
