"""
Count-only synthetic marker audit.

Never enumerates participant rows. Returns integer counts for every field
that could persist query text or a stuffed session key. A non-zero count is
a fail; the caller must not print or export the matching rows.
"""

from __future__ import annotations

import json

from .models import (
    DataInsert,
    EngagementEvent,
    MatchingEvaluation,
    NotRelevantReport,
    Prompt,
    Response,
    Setting,
    Topic,
    Trigger,
)

DEFAULT_MARKER = "S26GATE1MARKER_synthetic_not_a_person"


def _count_json_field(model, field, needle):
    low = needle.lower()
    n = 0
    for payload in model.objects.values_list(field, flat=True).iterator():
        if payload is None:
            continue
        if low in json.dumps(payload, default=str).lower():
            n += 1
    return n


def count_marker(marker=DEFAULT_MARKER):
    """Return {field: count} for icontains matches. Values are integers only."""
    needle = (marker or "").strip()
    if not needle:
        raise ValueError("marker must be non-empty")
    return {
        "notrelevantreport.user_search_query": NotRelevantReport.objects.filter(
            user_search_query__icontains=needle
        ).count(),
        "response.response_content": Response.objects.filter(
            response_content__icontains=needle
        ).count(),
        "engagementevent.session_key": EngagementEvent.objects.filter(
            session_key__icontains=needle
        ).count(),
        "prompt.prompt_content": Prompt.objects.filter(
            prompt_content__icontains=needle
        ).count(),
        "topic.name": Topic.objects.filter(name__icontains=needle).count(),
        "topic.description": Topic.objects.filter(description__icontains=needle).count(),
        "trigger.trigger_text": Trigger.objects.filter(trigger_text__icontains=needle).count(),
        "setting.value": Setting.objects.filter(value__icontains=needle).count(),
        "datainsert.create_triggers": DataInsert.objects.filter(
            create_triggers__icontains=needle
        ).count(),
        "matchingevaluation.notes": MatchingEvaluation.objects.filter(
            notes__icontains=needle
        ).count(),
        "matchingevaluation.results": _count_json_field(MatchingEvaluation, "results", needle),
    }


def audit_is_clean(counts):
    return all(int(value) == 0 for value in counts.values())
