"""
Build (or rebuild) the topic embedding index used by the query classifier.

    python manage.py build_topic_index [--force]

The index also rebuilds itself automatically when a Topic is edited, so this
command is mainly useful at deploy time and when debugging.
"""

from django.core.management.base import BaseCommand, CommandError

from researchdata.embedding import ClassifierUnavailable, build_topic_index, classifier_status


class Command(BaseCommand):
    help = 'Embed all Topics and cache the classifier index.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Rebuild even if the cached index is current.')

    def handle(self, *args, **options):
        try:
            matrix, topic_ids = build_topic_index(force=options['force'])
        except ClassifierUnavailable as e:
            raise CommandError(
                f'Classifier unavailable ({e}). Run "python manage.py download_model" first.'
            )
        if matrix is None:
            self.stdout.write(self.style.WARNING('No topics in the database; nothing to index.'))
            return
        self.stdout.write(self.style.SUCCESS(f'Index built: {len(topic_ids)} topics.'))
        self.stdout.write(str(classifier_status()))
