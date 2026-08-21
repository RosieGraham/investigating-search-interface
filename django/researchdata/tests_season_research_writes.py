"""
Failing contract: RESEARCH_WRITES_ENABLED default-deny, every method, no rows.

Gate 1: these tests must fail against origin/main because the write views
still create rows. The inverted control must pass on this baseline, proving
the helper detects a write.
"""

from django.test import TestCase, override_settings
from django.urls import reverse

from .models import EngagementEvent, Prompt, Topic, TopicGroup
from .tests_season_support import (
    BUILD_ID,
    MARKER,
    assert_json_error,
    assert_research_writes_enabled_setting_is_false,
    assert_research_writes_refused,
)


class ResearchWriteKillSwitchTests(TestCase):
    def setUp(self):
        group = TopicGroup.objects.create(name="G")
        topic = Topic.objects.create(topic_group=group, name="T", description="prose")
        self.prompt = Prompt.objects.create(
            topic=topic, prompt_content="p", admin_approved=True)

    def test_setting_defaults_to_false(self):
        assert_research_writes_enabled_setting_is_false()

    def test_all_methods_on_all_write_routes_are_refused(self):
        assert_research_writes_refused(self.client)

    def test_refusal_happens_before_field_parsing(self):
        """A body that would 400 if parsed (invalid event_type) must still be 403."""
        resp = self.client.post(
            reverse("researchdata:event-post"),
            {"event_type": "not_a_thing", "session_key": MARKER},
        )
        assert_json_error(resp, 403, "research_writes_disabled")
        self.assertEqual(EngagementEvent.objects.count(), 0)

    def test_workshop_identity_cannot_write_even_if_switch_true(self):
        """Immutable workshop policy denial: switch true must still refuse the workshop build."""
        try:
            from researchdata.release_policy import workshop_writes_allowed
        except ImportError as exc:
            self.fail(f"release_policy.workshop_writes_allowed is required: {exc}")
        self.assertFalse(workshop_writes_allowed(BUILD_ID))


class ResearchWriteInvertedControlTests(TestCase):
    """Planted fault: when writes are explicitly enabled, the helper must notice."""

    def setUp(self):
        group = TopicGroup.objects.create(name="G")
        topic = Topic.objects.create(topic_group=group, name="T", description="prose")
        self.prompt = Prompt.objects.create(
            topic=topic, prompt_content="p", admin_approved=True)

    @override_settings(RESEARCH_WRITES_ENABLED=True)
    def test_helper_fails_while_writes_are_open(self):
        with self.assertRaises(AssertionError):
            assert_research_writes_refused(self.client)

    @override_settings(RESEARCH_WRITES_ENABLED=True)
    def test_baseline_event_post_still_creates_a_row(self):
        """Preserve write behaviour only when the switch is explicitly on."""
        resp = self.client.post(reverse("researchdata:event-post"), {
            "event_type": "prompt_shown",
            "session_key": MARKER,
        })
        self.assertEqual(resp.json().get("event_saved"), 1)
        self.assertEqual(EngagementEvent.objects.filter(session_key=MARKER).count(), 1)
