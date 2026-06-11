"""
Apply topic descriptions from a JSON file to the database.

    python ../scripts/apply_topic_descriptions.py [path/to/descriptions.json]

The JSON maps Topic id -> description string (keys starting with "_" are
ignored). Defaults to data/topic-descriptions-draft.json. Descriptions can
also be edited directly in the Django admin; this script exists so batches
can be drafted in a text editor and applied in one go. Saving topics marks
the classifier index dirty, so matching picks the new text up automatically.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'django'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

import django  # noqa: E402

django.setup()

from researchdata.models import Topic  # noqa: E402

DEFAULT = Path(__file__).resolve().parents[1] / 'data' / 'topic-descriptions-draft.json'


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    if not path.exists():
        sys.exit(f'File not found: {path}')
    data = json.loads(path.read_text(encoding='utf-8'))

    applied, missing = 0, []
    for key, description in data.items():
        if key.startswith('_'):
            continue
        topic = Topic.objects.filter(id=int(key)).first()
        if not topic:
            missing.append(key)
            continue
        topic.description = description.strip()
        topic.save(update_fields=['description'])
        applied += 1

    total = Topic.objects.count()
    described = Topic.objects.exclude(description__isnull=True).exclude(description='').count()
    print(f'Applied {applied} descriptions. {described}/{total} topics now have one.')
    if missing:
        print(f'WARNING: no topic found for ids: {", ".join(missing)}')


if __name__ == '__main__':
    main()
