"""
Apply a new-content package (groups, topics with descriptions, approved
prompts, and fallback triggers) to the database.

Idempotent. Re-running updates existing rows rather than duplicating:
  - topic groups and topics are matched by their (unique) name
  - prompts are matched by the "ref:<REF>" token stored in admin_notes
  - triggers are matched by their (unique) text and linked, never cleared

It does NOT touch any existing topic, prompt or trigger that is not named in
the package, and it does NOT use the destructive M2M rebuild that
import_live_export performs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from researchdata.description_blob import split_blob
from researchdata.models import Prompt, Topic, TopicGroup, Trigger

Change = tuple[str, str, str, Any, Any]


def _topic_fields_from_row(topic_row: dict) -> dict:
    """Resolve a package topic row into the split Topic fields.

    Packages carry `description` (historically the full blob) and
    `example_queries` (now actually used: it was validated but ignored for
    weeks, which is exactly the kind of silent contract gap this system
    keeps growing). The blob is split; an explicit example_queries list
    wins over queries parsed from the blob.
    """
    prose, parsed_queries, contrasts = split_blob(topic_row["description"])
    explicit = [q.strip() for q in (topic_row.get("example_queries") or []) if q and q.strip()]
    return {
        "description": prose,
        "example_queries": explicit or parsed_queries,
        "contrasts": contrasts,
    }


@dataclass
class ApplyResult:
    created: dict[str, int] = field(
        default_factory=lambda: {"groups": 0, "topics": 0, "prompts": 0, "triggers": 0, "links": 0}
    )
    updated: dict[str, int] = field(
        default_factory=lambda: {"groups": 0, "topics": 0, "prompts": 0}
    )
    changes: list[Change] = field(default_factory=list)


def find_prompt_by_ref(ref: str) -> Prompt | None:
    # admin_notes stores "ref:<REF> | ...". The trailing space stops
    # ISI-S2-001 from matching ISI-S2-001a.
    return Prompt.objects.filter(admin_notes__icontains=f"ref:{ref} ").first()


def _record_change(changes: list[Change], kind: str, name_or_ref: str, field_name: str, before: Any, after: Any):
    if before != after:
        changes.append((kind, name_or_ref, field_name, before, after))


def count_unapproves(package: dict) -> int:
    """How many currently-approved prompts would become unapproved."""
    count = 0
    for row in package.get("prompts", []):
        existing = find_prompt_by_ref(row["ref"])
        if existing and existing.admin_approved and not bool(row.get("admin_approved")):
            count += 1
    return count


def validate_package(package: dict) -> None:
    """Raise ValidationError if the package JSON does not match the expected schema."""
    if not isinstance(package, dict):
        raise ValidationError("Package must be a JSON object.")

    for key in ("groups", "topics", "prompts"):
        if key not in package:
            raise ValidationError(f"Package is missing required key: {key!r}.")
        if not isinstance(package[key], list):
            raise ValidationError(f"Package key {key!r} must be a list.")

    group_names: set[str] = set()
    for index, group in enumerate(package["groups"]):
        if not isinstance(group, dict):
            raise ValidationError(f"groups[{index}] must be an object.")
        if not group.get("name"):
            raise ValidationError(f"groups[{index}] is missing a non-empty name.")
        group_names.add(group["name"])

    topic_names: set[str] = set()
    for index, topic in enumerate(package["topics"]):
        if not isinstance(topic, dict):
            raise ValidationError(f"topics[{index}] must be an object.")
        for req in ("name", "group", "description", "example_queries"):
            if req not in topic:
                raise ValidationError(f"topics[{index}] is missing required field {req!r}.")
        if topic["group"] not in group_names:
            raise ValidationError(
                f"topics[{index}] references unknown group {topic['group']!r}."
            )
        if not isinstance(topic["example_queries"], list):
            raise ValidationError(f"topics[{index}].example_queries must be a list.")
        topic_names.add(topic["name"])

    for index, prompt in enumerate(package["prompts"]):
        if not isinstance(prompt, dict):
            raise ValidationError(f"prompts[{index}] must be an object.")
        for req in ("ref", "topic", "style", "prompt_content", "admin_approved"):
            if req not in prompt:
                raise ValidationError(f"prompts[{index}] is missing required field {req!r}.")
        if prompt["topic"] not in topic_names:
            raise ValidationError(
                f"prompts[{index}] references unknown topic {prompt['topic']!r}."
            )
        triggers = prompt.get("triggers", [])
        if triggers is not None and not isinstance(triggers, list):
            raise ValidationError(f"prompts[{index}].triggers must be a list.")


@transaction.atomic
def apply_package(package: dict, *, dry_run: bool, actor=None) -> ApplyResult:
    validate_package(package)
    result = ApplyResult()

    # 1. Topic groups (by unique name)
    group_by_name: dict[str, TopicGroup] = {}
    for group_row in package["groups"]:
        obj, made = TopicGroup.objects.get_or_create(name=group_row["name"])
        if made:
            result.created["groups"] += 1
        notes = group_row.get("admin_notes")
        if notes and obj.admin_notes != notes:
            _record_change(result.changes, "group", obj.name, "admin_notes", obj.admin_notes, notes)
            obj.admin_notes = notes
            obj.save(update_fields=["admin_notes"])
            if not made:
                result.updated["groups"] += 1
        group_by_name[group_row["name"]] = obj

    # 2. Topics (by unique name), blob split into prose / examples / contrasts
    topic_by_name: dict[str, Topic] = {}
    for topic_row in package["topics"]:
        group = group_by_name.get(topic_row["group"]) or TopicGroup.objects.get(name=topic_row["group"])
        split_fields = _topic_fields_from_row(topic_row)
        obj = Topic.objects.filter(name=topic_row["name"]).first()
        if obj is None:
            obj = Topic(
                name=topic_row["name"],
                topic_group=group,
                admin_notes=topic_row.get("admin_notes") or None,
                **split_fields,
            )
            if not dry_run:
                obj.save()
            result.created["topics"] += 1
            for field_name, after in split_fields.items():
                _record_change(result.changes, "topic", topic_row["name"], field_name, None, after)
        else:
            changed_fields: list[str] = []
            if obj.topic_group_id != group.id:
                _record_change(
                    result.changes,
                    "topic",
                    obj.name,
                    "topic_group",
                    str(obj.topic_group),
                    str(group),
                )
                obj.topic_group = group
                changed_fields.append("topic_group")
            for field_name, after in split_fields.items():
                before = getattr(obj, field_name)
                normalised_before = before or ("" if field_name != "example_queries" else [])
                if normalised_before != after:
                    _record_change(result.changes, "topic", obj.name, field_name, before, after)
                    setattr(obj, field_name, after)
                    changed_fields.append(field_name)
            new_notes = topic_row.get("admin_notes") or None
            if (obj.admin_notes or None) != new_notes:
                _record_change(
                    result.changes, "topic", obj.name, "admin_notes", obj.admin_notes, new_notes
                )
                obj.admin_notes = new_notes
                changed_fields.append("admin_notes")
            if changed_fields and not dry_run:
                obj.save(update_fields=changed_fields)
            if changed_fields:
                result.updated["topics"] += 1
        topic_by_name[topic_row["name"]] = obj

    # 3. Prompts (idempotent by ref token in admin_notes) + 4. triggers
    for prompt_row in package["prompts"]:
        topic = topic_by_name[prompt_row["topic"]]
        ref = prompt_row["ref"]
        existing = find_prompt_by_ref(ref)
        admin_notes = prompt_row.get("admin_notes") or f"ref:{ref} "
        fields = dict(
            topic=topic,
            prompt_content=prompt_row["prompt_content"],
            priority=prompt_row.get("priority"),
            admin_approved=bool(prompt_row.get("admin_approved")),
            response_required=bool(prompt_row.get("response_required")),
            seeed_url=prompt_row.get("seeed_url") or None,
            admin_notes=admin_notes,
        )
        if existing is None:
            prompt = Prompt(**fields)
            if not dry_run:
                prompt.save()
            result.created["prompts"] += 1
            for field_name, after in fields.items():
                if field_name == "topic":
                    _record_change(result.changes, "prompt", ref, field_name, None, str(topic))
                else:
                    _record_change(result.changes, "prompt", ref, field_name, None, after)
        else:
            for field_name, after in fields.items():
                before = getattr(existing, field_name)
                if field_name == "topic":
                    before = str(before)
                    after = str(after)
                _record_change(result.changes, "prompt", ref, field_name, before, after)
                setattr(existing, field_name, fields[field_name])
            if not dry_run:
                existing.save()
            prompt = existing
            result.updated["prompts"] += 1

        for text in prompt_row.get("triggers", []):
            text = text.strip()
            if not text:
                continue
            trig = Trigger.objects.filter(trigger_text=text).first()
            if trig is None:
                trig = Trigger(trigger_text=text)
                if not dry_run:
                    trig.save()
                result.created["triggers"] += 1
            if not dry_run and prompt.pk and not prompt.triggers.filter(pk=trig.pk).exists():
                prompt.triggers.add(trig)
                result.created["links"] += 1

    if dry_run:
        transaction.set_rollback(True)
    return result
