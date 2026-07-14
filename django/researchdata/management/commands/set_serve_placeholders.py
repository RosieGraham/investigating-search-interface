"""
Turn the SERVE_PLACEHOLDERS evaluation flag on or off.

    python manage.py set_serve_placeholders true
    python manage.py set_serve_placeholders false

When on, prompt_get serves a placeholder card (matched_by='placeholder')
for a topic that matches above threshold but has no approved prompt yet,
instead of silently skipping it. Evaluation scaffold for Emilia's two-week
window; see Working Docs/Placeholder-card serving toggle - build spec -
13 July 2026.md. Default is off; turn off again before any public/demo
showing (e.g. the SEASON conference).
"""

from django.core.management.base import BaseCommand, CommandError

from researchdata.classifier_config import set_serve_placeholders

TRUE_VALUES = {'true', 'on'}
FALSE_VALUES = {'false', 'off'}


class Command(BaseCommand):
    help = "Turn the SERVE_PLACEHOLDERS evaluation flag on or off ('true'/'false' or 'on'/'off')."

    def add_arguments(self, parser):
        parser.add_argument('state', help="'true'/'on' to enable, 'false'/'off' to disable.")

    def handle(self, *args, **options):
        state = options['state'].strip().lower()
        if state in TRUE_VALUES:
            on = True
        elif state in FALSE_VALUES:
            on = False
        else:
            raise CommandError(
                f"Unrecognised value '{options['state']}'; use 'true'/'false' or 'on'/'off'."
            )

        set_serve_placeholders(on)
        self.stdout.write(self.style.SUCCESS(
            f"SERVE_PLACEHOLDERS set to {'true' if on else 'false'}."
        ))
