"""
Failing contract: one prompt from the global top topic; silence, not fall-through.

Exclusions stay (amendment 1). An excluded top topic is silence, including
when placeholders are on (amendment 4).
"""

from unittest import mock

import numpy as np
from django.core.cache import cache
from django.test import TestCase, override_settings

from .classifier_config import set_serve_placeholders
from .models import Prompt, Topic, TopicGroup
from .tests_season_support import match_post


class StrictTopOneServingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.group_a = TopicGroup.objects.create(name="Group A")
        self.group_b = TopicGroup.objects.create(name="Group B")
        self.top = Topic.objects.create(
            topic_group=self.group_a, name="Top topic", description="prose")
        self.second = Topic.objects.create(
            topic_group=self.group_b, name="Second topic", description="prose")
        self.top_prompt = Prompt.objects.create(
            topic=self.top, prompt_content="Top prompt",
            priority=40, admin_approved=True)
        self.top_prompt_low = Prompt.objects.create(
            topic=self.top, prompt_content="Top prompt low",
            priority=10, admin_approved=True)
        self.second_prompt = Prompt.objects.create(
            topic=self.second, prompt_content="Second prompt",
            priority=99, admin_approved=True)
        self.promptless = Topic.objects.create(
            topic_group=self.group_a, name="Promptless top", description="prose")

    def _post(self, matches, extra_fields=None):
        with mock.patch("researchdata.views.embed_query", return_value=np.zeros(4)), \
             mock.patch("researchdata.views.classify_query", return_value=matches), \
             mock.patch("researchdata.views.rank_prompts", side_effect=lambda _qv, ids: [(pid, 0.5) for pid in ids]):
            return match_post(self.client, "anything", extra_fields=extra_fields)

    @override_settings(TRIGGER_FALLBACK_ENABLED=False)
    def test_prompt_rich_top_two_returns_exactly_one_prompt_from_top_topic(self):
        set_serve_placeholders(False)
        resp = self._post([(self.top.id, 0.80), (self.second.id, 0.70)])
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["prompts"]), 1)
        self.assertEqual(data["prompts"][0]["id"], self.top_prompt.id)
        self.assertEqual(data["prompts"][0]["topic_id"], self.top.id)
        self.assertEqual(data["prompt"], data["prompts"][0])
        self.assertNotIn(self.second_prompt.id, [p["id"] for p in data["prompts"]])

    @override_settings(TRIGGER_FALLBACK_ENABLED=False)
    def test_promptless_top_does_not_fall_through_to_prompt_rich_second(self):
        set_serve_placeholders(False)
        resp = self._post([(self.promptless.id, 0.80), (self.second.id, 0.70)])
        data = resp.json()
        self.assertEqual(data["prompts"], [])
        self.assertIs(data["prompt"], False)

    @override_settings(TRIGGER_FALLBACK_ENABLED=False)
    def test_excluded_top_topic_is_silence_placeholders_off(self):
        set_serve_placeholders(False)
        resp = self._post(
            [(self.top.id, 0.80), (self.second.id, 0.70)],
            extra_fields={"topics_exclude": str(self.group_a.id)},
        )
        data = resp.json()
        self.assertEqual(data["prompts"], [])
        self.assertIs(data["prompt"], False)

    @override_settings(TRIGGER_FALLBACK_ENABLED=False)
    def test_excluded_top_topic_is_silence_placeholders_on(self):
        set_serve_placeholders(True)
        resp = self._post(
            [(self.top.id, 0.80), (self.second.id, 0.70)],
            extra_fields={"topics_exclude": str(self.group_a.id)},
        )
        data = resp.json()
        self.assertEqual(data["prompts"], [])
        self.assertIs(data["prompt"], False)
        if data["prompts"]:
            self.assertNotEqual(data["prompts"][0].get("matched_by"), "placeholder")

    @override_settings(TRIGGER_FALLBACK_ENABLED=False)
    def test_unapproved_only_top_is_silence(self):
        set_serve_placeholders(False)
        self.top_prompt.admin_approved = False
        self.top_prompt.save()
        self.top_prompt_low.admin_approved = False
        self.top_prompt_low.save()
        resp = self._post([(self.top.id, 0.80), (self.second.id, 0.70)])
        data = resp.json()
        self.assertEqual(data["prompts"], [])
        self.assertIs(data["prompt"], False)


class TopOneInvertedControlTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        group_a = TopicGroup.objects.create(name="Group A")
        group_b = TopicGroup.objects.create(name="Group B")
        self.promptless = Topic.objects.create(
            topic_group=group_a, name="Promptless top", description="prose")
        self.second = Topic.objects.create(
            topic_group=group_b, name="Second topic", description="prose")
        self.second_prompt = Prompt.objects.create(
            topic=self.second, prompt_content="Second prompt",
            admin_approved=True)

    @override_settings(TRIGGER_FALLBACK_ENABLED=False)
    def test_silence_assertion_fails_while_fall_through_exists(self):
        """Planted fault: skipping the global top topic must fail the silence assertion."""
        set_serve_placeholders(False)

        def plant_second(matches):
            return matches[1] if len(matches) > 1 else matches[0]

        with mock.patch("researchdata.views.select_top_topic", side_effect=plant_second), \
             mock.patch("researchdata.views.embed_query", return_value=np.zeros(4)), \
             mock.patch("researchdata.views.classify_query",
                        return_value=[(self.promptless.id, 0.80), (self.second.id, 0.70)]), \
             mock.patch("researchdata.views.rank_prompts",
                        side_effect=lambda _qv, ids: [(pid, 0.5) for pid in ids]):
            data = match_post(self.client, "anything").json()
        self.assertEqual(data["prompts"][0]["id"], self.second_prompt.id)
        with self.assertRaises(AssertionError):
            self.assertEqual(data["prompts"], [])
            self.assertIs(data["prompt"], False)
