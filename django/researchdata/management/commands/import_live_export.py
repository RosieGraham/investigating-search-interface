"""
Import the data exported from the original bham.ac.uk deployment.

    python manage.py import_live_export [path/to/export.json]

The export file is produced by scripts/export_from_admin.js (run in the
browser on the old Django admin) and contains topic_groups, topics, prompts
(with trigger_ids) and triggers. Original primary keys are preserved so that
references remain stable across systems.

Idempotent: running it again updates existing rows rather than duplicating.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from researchdata import models

DEFAULT_PATH = Path(__file__).resolve().parents[4] / 'data' / 'live-export-2026-06-11.json'


class Command(BaseCommand):
    help = 'Import topic groups, topics, prompts and triggers from a live-export JSON file.'

    def add_arguments(self, parser):
        parser.add_argument('path', nargs='?', default=str(DEFAULT_PATH), help='Path to the export JSON.')

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(options['path'])
        if not path.exists():
            raise CommandError(f'Export file not found: {path}')
        data = json.loads(path.read_text(encoding='utf-8'))

        for key in ('topic_groups', 'topics', 'prompts', 'triggers'):
            if key not in data:
                raise CommandError(f'Export file is missing the "{key}" key.')

        # 1. Topic groups
        for row in data['topic_groups']:
            models.TopicGroup.objects.update_or_create(id=row['id'], defaults={'name': row['name']})

        # 2. Topics
        for row in data['topics']:
            models.Topic.objects.update_or_create(
                id=row['id'],
                defaults={
                    'name': row['name'],
                    'topic_group_id': row['topic_group_id'],
                    'admin_notes': row.get('admin_notes') or None,
                },
            )

        # 3. Triggers
        for row in data['triggers']:
            models.Trigger.objects.update_or_create(id=row['id'], defaults={'trigger_text': row['text']})

        # 4. Prompts + trigger links
        for row in data['prompts']:
            prompt, _ = models.Prompt.objects.update_or_create(
                id=row['id'],
                defaults={
                    'topic_id': row['topic_id'],
                    'prompt_content': row['prompt_content'],
                    'response_required': bool(row.get('response_required')),
                    'priority': row.get('priority'),
                    'admin_approved': bool(row.get('admin_approved')),
                    'admin_notes': row.get('admin_notes') or None,
                },
            )
            prompt.triggers.set(row.get('trigger_ids', []))

        # 5. Reset Postgres sequences after explicit-PK inserts so future
        #    admin-created rows don't collide with imported ids.
        if connection.vendor == 'postgresql':
            targets = [models.TopicGroup, models.Topic, models.Trigger, models.Prompt]
            with connection.cursor() as cursor:
                for model in targets:
                    table = model._meta.db_table
                    cursor.execute(
                        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                        f"(SELECT COALESCE(MAX(id), 1) FROM {table}));"
                    )

        self.stdout.write(self.style.SUCCESS(
            'Imported: {} topic groups, {} topics, {} triggers, {} prompts.'.format(
                models.TopicGroup.objects.count(),
                models.Topic.objects.count(),
                models.Trigger.objects.count(),
                models.Prompt.objects.count(),
            )
        ))
        self.stdout.write(
            'Note: prompt approval states were preserved. Only admin-approved prompts are served by the API.'
        )
