# Phase 3 of MATCHING-QUALITY-BRIEF.md: split the single-blob Topic
# description into prose (description), example_queries (JSON list) and
# contrasts (never embedded). Reversible; the forward migration asserts that
# recombining the three fields reproduces the original blob byte-exactly for
# every row and fails loudly if it does not.
#
# The splitter is frozen into this migration on purpose: migrations must not
# import live app code that can drift.

import re

import django.db.models
from django.db import migrations, models

_SPLIT = re.compile(
    r"^(?P<prose>.*?)"
    r"(?:\s+(?P<examples>Example queries:.*?\.))?"
    r"(?:\s+(?P<contrasts>Distinct from .*))?$",
    re.S,
)
_PREFIX = "Example queries:"


def _split(blob):
    match = _SPLIT.match(blob)
    prose = match.group("prose") or ""
    seg = match.group("examples") or ""
    contrasts = match.group("contrasts") or ""
    queries = []
    if seg:
        inner = seg[len(_PREFIX):].strip()
        if inner.endswith("."):
            inner = inner[:-1]
        queries = [q.strip() for q in inner.split(",") if q.strip()]
    return prose, queries, contrasts


def _recombine(prose, queries, contrasts):
    parts = [prose] if prose else []
    if queries:
        parts.append(f"{_PREFIX} {', '.join(queries)}.")
    if contrasts:
        parts.append(contrasts)
    return " ".join(parts)


def forwards(apps, schema_editor):
    Topic = apps.get_model("researchdata", "Topic")
    failures = []
    for topic in Topic.objects.exclude(description__isnull=True).exclude(description=""):
        blob = topic.description.strip()
        prose, queries, contrasts = _split(blob)
        if _recombine(prose, queries, contrasts) != blob:
            failures.append((topic.id, topic.name, blob[:80]))
            continue
        # .update() skips auto_now and signals: a migration is not an edit.
        Topic.objects.filter(id=topic.id).update(
            description=prose,
            example_queries=queries,
            contrasts=contrasts,
        )
    if failures:
        raise RuntimeError(
            "Refusing to migrate: these topic descriptions do not recombine "
            f"byte-exactly after splitting: {failures}"
        )


def backwards(apps, schema_editor):
    Topic = apps.get_model("researchdata", "Topic")
    for topic in Topic.objects.all():
        queries = topic.example_queries or []
        contrasts = topic.contrasts or ""
        if not (topic.description or queries or contrasts):
            continue
        blob = _recombine((topic.description or "").strip(), queries, contrasts)
        Topic.objects.filter(id=topic.id).update(description=blob)


class Migration(migrations.Migration):

    dependencies = [
        ("researchdata", "0008_matchingevaluation"),
    ]

    operations = [
        migrations.AddField(
            model_name="topic",
            name="example_queries",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "Real query phrasings this topic should catch, as a JSON "
                    "list of strings. Each is embedded as its own vector and "
                    "blended with the description score. One careless entry "
                    "here can capture other topics' traffic, so the content "
                    "pipeline self-retrieval check must pass before these ship."
                ),
            ),
        ),
        migrations.AddField(
            model_name="topic",
            name="contrasts",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "Editorial notes on adjacent topics ('Distinct from X "
                    "(...), from Y (...)'). NEVER embedded: this text exists "
                    "to keep human editors from writing overlapping topics, "
                    "and embedding it injects the competitors' vocabulary "
                    "into this topic's vector."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="topic",
            name="description",
            field=models.TextField(
                blank=True,
                null=True,
                help_text=(
                    "Clean prose describing the topic, used by the vector "
                    "classifier. Prose ONLY: example queries live in their "
                    "own field below, and contrasts ('Distinct from ...') in "
                    "theirs. The better this prose captures what the topic "
                    "is about, the better the matching. Topics without a "
                    "description fall back to '[group]: [name]' for "
                    "matching, which is much weaker."
                ),
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
