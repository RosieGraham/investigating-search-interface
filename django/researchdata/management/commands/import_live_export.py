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
        models.TopicGroup.objects.bulk_create(
            [models.TopicGroup(id=row['id'], name=row['name']) for row in data['topic_groups']],
            update_conflicts=True, update_fields=['name'], unique_fields=['id'],
        )

        # 2. Topics
        models.Topic.objects.bulk_create(
            [models.Topic(
                id=row['id'],
                name=row['name'],
                topic_group_id=row['topic_group_id'],
                admin_notes=row.get('admin_notes') or None,
            ) for row in data['topics']],
            update_conflicts=True, update_fields=['name', 'topic_group_id', 'admin_notes'], unique_fields=['id'],
        )

        # 3. Triggers — bulk insert in batches of 1000
        trigger_objs = [
            models.Trigger(id=row['id'], trigger_text=row['text']) for row in data['triggers']
        ]
        for i in range(0, len(trigger_objs), 1000):
            models.Trigger.objects.bulk_create(
                trigger_objs[i:i+1000],
                update_conflicts=True, update_fields=['trigger_text'], unique_fields=['id'],
            )

        # 4. Prompts — bulk insert, then set M2M trigger links in batches
        prompt_objs = [
            models.Prompt(
                id=row['id'],
                topic_id=row['topic_id'],
                prompt_content=row['prompt_content'],
                response_required=bool(row.get('response_required')),
                priority=row.get('priority'),
                admin_approved=bool(row.get('admin_approved')),
                admin_notes=row.get('admin_notes') or None,
            ) for row in data['prompts']
        ]
        models.Prompt.objects.bulk_create(
            prompt_objs,
            update_conflicts=True,
            update_fields=['topic_id', 'prompt_content', 'response_required', 'priority', 'admin_approved', 'admin_notes'],
            unique_fields=['id'],
        )
        # M2M trigger links — clear and re-add using the through table directly
        ThroughModel = models.Prompt.triggers.through
        prompt_trigger_rows = []
        for row in data['prompts']:
            for tid in row.get('trigger_ids', []):
                prompt_trigger_rows.append(ThroughModel(prompt_id=row['id'], trigger_id=tid))
        ThroughModel.objects.all().delete()
        for i in range(0, len(prompt_trigger_rows), 1000):
            ThroughModel.objects.bulk_create(prompt_trigger_rows[i:i+1000], ignore_conflicts=True)

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
