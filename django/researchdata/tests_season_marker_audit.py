"""
Count-only synthetic marker audit: matching and refused writes leave no row,
and the auditor is not vacuously clean.
"""

from django.test import TestCase, override_settings

from .marker_audit import DEFAULT_MARKER, audit_is_clean, count_marker
from .models import NotRelevantReport, Prompt, Topic, TopicGroup
from .tests_season_support import WRITE_ROUTES, match_post, workshop_headers


class SyntheticMarkerAuditTests(TestCase):
    def test_matching_and_refused_writes_leave_zero_counts(self):
        match_post(self.client, DEFAULT_MARKER)
        for path in WRITE_ROUTES:
            self.client.post(path, {"payload": DEFAULT_MARKER}, **workshop_headers())
        counts = count_marker(DEFAULT_MARKER)
        self.assertTrue(audit_is_clean(counts), counts)
        self.assertTrue(all(isinstance(value, int) for value in counts.values()))

    def test_auditor_fails_when_a_report_row_is_planted(self):
        group = TopicGroup.objects.create(name="Audit group")
        topic = Topic.objects.create(topic_group=group, name="Audit topic")
        prompt = Prompt.objects.create(
            topic=topic, prompt_content="not the marker", admin_approved=True
        )
        NotRelevantReport.objects.create(
            prompt=prompt,
            user_search_query=DEFAULT_MARKER,
        )
        counts = count_marker(DEFAULT_MARKER)
        self.assertFalse(audit_is_clean(counts))
        self.assertGreater(counts["notrelevantreport.user_search_query"], 0)
        self.assertEqual(set(counts), set(count_marker(DEFAULT_MARKER)))


class SyntheticMarkerAuditInvertedTests(TestCase):
    @override_settings(RESEARCH_WRITES_ENABLED=True)
    def test_write_enabled_report_is_detected_by_count_only_audit(self):
        group = TopicGroup.objects.create(name="Audit group")
        topic = Topic.objects.create(topic_group=group, name="Audit topic")
        prompt = Prompt.objects.create(
            topic=topic, prompt_content="not the marker", admin_approved=True
        )
        resp = self.client.post(
            "/data/notrelevantreport/post/",
            {
                "active_prompt_id": str(prompt.id),
                "user_search_query": DEFAULT_MARKER,
                "classifier_confidence": "0.9",
            },
        )
        self.assertEqual(resp.status_code, 200)
        counts = count_marker(DEFAULT_MARKER)
        self.assertFalse(
            audit_is_clean(counts),
            "planted write must make the count-only auditor fail",
        )
