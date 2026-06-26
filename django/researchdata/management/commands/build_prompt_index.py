"""
Build (or rebuild) the prompt embedding index used for semantic prompt ranking.

    python manage.py build_prompt_index [--force]

The index rebuilds automatically when a Prompt is saved or deleted via the
admin, so this command is mainly useful at deploy time and for debugging.
"""

from django.core.management.base import BaseCommand, CommandError

from researchdata.embedding import ClassifierUnavailable, build_prompt_index


class Command(BaseCommand):
    help = 'Embed all approved Prompts and cache the prompt ranking index.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Rebuild even if the cached index is current.')

    def handle(self, *args, **options):
        try:
            matrix, prompt_ids = build_prompt_index(force=options['force'])
        except ClassifierUnavailable as e:
            raise CommandError(
                f'Classifier unavailable ({e}). Run "python manage.py download_model" first.'
            )
        if matrix is None:
            self.stdout.write(self.style.WARNING('No approved prompts in the database; nothing to index.'))
            return
        self.stdout.write(self.style.SUCCESS(f'Prompt index built: {len(prompt_ids)} prompts.'))
