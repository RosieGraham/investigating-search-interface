"""
Count-only audit for a synthetic marker query.

    python manage.py audit_synthetic_marker
    python manage.py audit_synthetic_marker --marker S26GATE1MARKER_synthetic_not_a_person

Prints field counts only. Never lists or exports matching rows.
Exit 1 if any count is non-zero.
Use this against a disposable database. Do not point it at production.
"""

from django.core.management.base import BaseCommand, CommandError

from researchdata.marker_audit import DEFAULT_MARKER, audit_is_clean, count_marker


class Command(BaseCommand):
    help = "Count-only: report whether a synthetic marker persists in query-shaped fields."

    def add_arguments(self, parser):
        parser.add_argument("--marker", default=DEFAULT_MARKER)
        parser.add_argument(
            "--allow-production",
            action="store_true",
            help="Required if DATABASE_URL looks like a hosted database. Default is to refuse.",
        )

    def handle(self, *args, **options):
        from django.conf import settings

        db = (settings.DATABASES.get("default") or {}).get("NAME") or ""
        engine = (settings.DATABASES.get("default") or {}).get("ENGINE") or ""
        hosted = "postgres" in engine or "mysql" in engine or "neon" in str(db).lower()
        if hosted and not options["allow_production"]:
            raise CommandError(
                "Refusing to scan a hosted database. Use a disposable sqlite copy. "
                "Pass --allow-production only with Rosie's approval."
            )
        counts = count_marker(options["marker"])
        dirty = []
        for field, count in counts.items():
            self.stdout.write(f"{field}={count}")
            if count:
                dirty.append(field)
        if not audit_is_clean(counts):
            raise CommandError(
                "marker present in: " + ", ".join(dirty) + " (counts only; rows not listed)"
            )
        self.stdout.write("marker_absent=true")
