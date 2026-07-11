"""
Read-only account audit: list every account with its privilege flags.

Prints, never modifies. Run it before and after any change to account
handling so the before/after picture is on the record:

    python manage.py audit_accounts

Roles as the dashboard enforces them (July 2026):
  superuser      full access, including participant data and user management
  staff-editor   dashboard login, content read-only, no participant data
  no-dashboard   cannot log into the admin at all
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Read-only: list every account with its privilege flags. Never modifies anything.'

    def handle(self, *args, **options):
        User = get_user_model()
        users = User.objects.order_by('id')

        counts = {'superuser': 0, 'staff-editor': 0, 'no-dashboard': 0, 'inactive': 0}
        self.stdout.write(f"{'id':>4}  {'role':<14}{'active':<8}{'last login':<18}username (= login email)")
        self.stdout.write('-' * 78)
        for user in users:
            if user.is_superuser:
                role = 'superuser'
            elif user.is_staff:
                role = 'staff-editor'
            else:
                role = 'no-dashboard'
            counts[role] += 1
            if not user.is_active:
                counts['inactive'] += 1
            last_login = user.last_login.strftime('%Y-%m-%d %H:%M') if user.last_login else 'never'
            active = 'yes' if user.is_active else 'NO'
            self.stdout.write(f"{user.id:>4}  {role:<14}{active:<8}{last_login:<18}{user.username}")

        self.stdout.write('-' * 78)
        self.stdout.write(
            f"{users.count()} account(s): {counts['superuser']} superuser, "
            f"{counts['staff-editor']} staff-editor, {counts['no-dashboard']} no-dashboard, "
            f"{counts['inactive']} inactive."
        )
