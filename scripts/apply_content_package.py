"""
Apply a new-content package (groups, topics with descriptions, approved
prompts, and fallback triggers) to the database.

    python ../scripts/apply_content_package.py [path/to/package.json] [--dry-run]

Defaults to data/new-content-package.json. Companion to
apply_topic_descriptions.py: that script only updates descriptions on topics
that already exist; this one also CREATES the new topic groups, topics and
approved prompts that the search/tools/ethics direction needs, because a
prompt cannot fire until its topic exists with a description.

Saving topics marks the classifier index dirty, so matching picks up the new
descriptions automatically; an explicit rebuild is attempted at the end and
skipped cleanly if the model is absent.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "django"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

import django  # noqa: E402

django.setup()

from researchdata.models import Prompt, Topic  # noqa: E402
from researchdata.services.content_apply import apply_package, validate_package  # noqa: E402

DEFAULT = Path(__file__).resolve().parents[1] / "data" / "new-content-package.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=str(DEFAULT), help="Path to the package JSON.")
    ap.add_argument("--dry-run", action="store_true", help="Roll back at the end; report what would change.")
    args = ap.parse_args()

    path = Path(args.path)
    if not path.exists():
        sys.exit(f"File not found: {path}")
    package = json.loads(path.read_text(encoding="utf-8"))
    validate_package(package)

    result = apply_package(package, dry_run=args.dry_run)

    tag = "[DRY RUN] " if args.dry_run else ""
    print(f"{tag}created: {result.created}")
    print(f"{tag}updated: {result.updated}")
    if result.changes:
        print(f"{tag}changes: {len(result.changes)} field updates recorded")

    total_topics = Topic.objects.count()
    described = Topic.objects.exclude(description__isnull=True).exclude(description="").count()
    approved = Prompt.objects.filter(admin_approved=True).count()
    print(
        f"{tag}DB now: {total_topics} topics, {described} with a description, "
        f"{approved} approved prompts."
    )

    # Rebuild the classifier index so matching reflects the new topics now,
    # rather than waiting for the first query. Skips cleanly without the model.
    if not args.dry_run:
        try:
            from researchdata.embedding import build_topic_index, ClassifierUnavailable

            try:
                _, ids = build_topic_index(force=True)
                print(f"Classifier index rebuilt: {len(ids)} topics.")
            except ClassifierUnavailable as e:
                print(f"Index not rebuilt now ({e}); it rebuilds lazily on the next query.")
        except Exception as e:  # pragma: no cover
            print(f"Index rebuild skipped ({e}).")


if __name__ == "__main__":
    main()
