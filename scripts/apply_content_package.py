"""
Apply a new-content package (groups, topics with descriptions, approved
prompts, and fallback triggers) to the database.

    python ../scripts/apply_content_package.py [path/to/package.json] [--dry-run]

Defaults to data/new-content-package.json. Companion to
apply_topic_descriptions.py: that script only updates descriptions on topics
that already exist; this one also CREATES the new topic groups, topics and
approved prompts that the search/tools/ethics direction needs, because a
prompt cannot fire until its topic exists with a description.

Idempotent. Re-running updates existing rows rather than duplicating:
  - topic groups and topics are matched by their (unique) name
  - prompts are matched by the "ref:<REF>" token stored in admin_notes
  - triggers are matched by their (unique) text and linked, never cleared

It does NOT touch any existing topic, prompt or trigger that is not named in
the package, and it does NOT use the destructive M2M rebuild that
import_live_export performs. Saving topics marks the classifier index dirty,
so matching picks up the new descriptions automatically; an explicit
rebuild is attempted at the end and skipped cleanly if the model is absent.
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

from django.db import transaction  # noqa: E402
from researchdata.models import TopicGroup, Topic, Prompt, Trigger  # noqa: E402

DEFAULT = Path(__file__).resolve().parents[1] / "data" / "new-content-package.json"


def ref_of(prompt_row):
    return prompt_row["ref"]


def find_prompt_by_ref(ref):
    # admin_notes stores "ref:<REF> | ...". The trailing space stops
    # ISI-S2-001 from matching ISI-S2-001a.
    return Prompt.objects.filter(admin_notes__icontains=f"ref:{ref} ").first()


@transaction.atomic
def apply(package, dry_run=False):
    created = {"groups": 0, "topics": 0, "prompts": 0, "triggers": 0, "links": 0}
    updated = {"groups": 0, "topics": 0, "prompts": 0}

    # 1. Topic groups (by unique name)
    group_by_name = {}
    for g in package["groups"]:
        obj, made = TopicGroup.objects.get_or_create(name=g["name"])
        if made:
            created["groups"] += 1
        if g.get("admin_notes") and obj.admin_notes != g["admin_notes"]:
            obj.admin_notes = g["admin_notes"]
            obj.save(update_fields=["admin_notes"])
            if not made:
                updated["groups"] += 1
        group_by_name[g["name"]] = obj

    # 2. Topics (by unique name) with description
    topic_by_name = {}
    for t in package["topics"]:
        group = group_by_name.get(t["group"]) or TopicGroup.objects.get(name=t["group"])
        obj = Topic.objects.filter(name=t["name"]).first()
        if obj is None:
            obj = Topic(name=t["name"], topic_group=group,
                        description=t["description"], admin_notes=t.get("admin_notes") or None)
            if not dry_run:
                obj.save()
            created["topics"] += 1
        else:
            changed = []
            if obj.topic_group_id != group.id:
                obj.topic_group = group; changed.append("topic_group")
            if (obj.description or "") != t["description"]:
                obj.description = t["description"]; changed.append("description")
            if (obj.admin_notes or None) != (t.get("admin_notes") or None):
                obj.admin_notes = t.get("admin_notes") or None; changed.append("admin_notes")
            if changed and not dry_run:
                obj.save(update_fields=changed)
            if changed:
                updated["topics"] += 1
        topic_by_name[t["name"]] = obj

    # 3. Prompts (idempotent by ref token in admin_notes) + 4. triggers
    for p in package["prompts"]:
        topic = topic_by_name[p["topic"]]
        existing = find_prompt_by_ref(p["ref"])
        fields = dict(
            topic=topic,
            prompt_content=p["prompt_content"],
            priority=p.get("priority"),
            admin_approved=bool(p.get("admin_approved")),
            response_required=bool(p.get("response_required")),
            seeed_url=p.get("seeed_url") or None,
            admin_notes=p["admin_notes"],
        )
        if existing is None:
            prompt = Prompt(**fields)
            if not dry_run:
                prompt.save()
            created["prompts"] += 1
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
            if not dry_run:
                existing.save()
            prompt = existing
            updated["prompts"] += 1

        # triggers (fallback path): get_or_create by unique text, link M2M
        for text in p.get("triggers", []):
            text = text.strip()
            if not text:
                continue
            trig = Trigger.objects.filter(trigger_text=text).first()
            if trig is None:
                trig = Trigger(trigger_text=text)
                if not dry_run:
                    trig.save()
                created["triggers"] += 1
            if not dry_run and prompt.pk and not prompt.triggers.filter(pk=trig.pk).exists():
                prompt.triggers.add(trig)
                created["links"] += 1

    if dry_run:
        transaction.set_rollback(True)
    return created, updated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=str(DEFAULT), help="Path to the package JSON.")
    ap.add_argument("--dry-run", action="store_true", help="Roll back at the end; report what would change.")
    args = ap.parse_args()

    path = Path(args.path)
    if not path.exists():
        sys.exit(f"File not found: {path}")
    package = json.loads(path.read_text(encoding="utf-8"))

    created, updated = apply(package, dry_run=args.dry_run)

    tag = "[DRY RUN] " if args.dry_run else ""
    print(f"{tag}created: {created}")
    print(f"{tag}updated: {updated}")

    total_topics = Topic.objects.count()
    described = Topic.objects.exclude(description__isnull=True).exclude(description="").count()
    approved = Prompt.objects.filter(admin_approved=True).count()
    print(f"{tag}DB now: {total_topics} topics, {described} with a description, "
          f"{approved} approved prompts.")

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
