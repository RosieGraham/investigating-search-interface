"""
Tests for the SERVE_PLACEHOLDERS evaluation-scaffold flag (see Working
Docs/Placeholder-card serving toggle - build spec - 13 July 2026.md).

classify_query and embed_query are patched out (as in
tests_priority_order.py) so the test drives prompt_get's placeholder
branch directly, without ONNX. rank_prompts is patched too, since it is
still reached on the real-prompt path.
"""

from unittest import mock
from urllib.parse import urlencode

import numpy as np
from django.core.cache import cache
from django.test import TestCase

from .classifier_config import set_serve_placeholders
from .models import Prompt, Topic, TopicGroup
from .tests_season_support import MATCH_PATH, workshop_headers


class ServePlaceholdersTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

        group = TopicGroup.objects.create(name="G")
        # Matches above threshold but has no approved prompt.
        self.promptless_topic = Topic.objects.create(
            topic_group=group, name="Vaccination", description="prose")
        # Matches above threshold and has an approved prompt.
        self.real_topic = Topic.objects.create(
            topic_group=group, name="Concentration and defaults", description="prose")
        self.real_prompt = Prompt.objects.create(
            topic=self.real_topic, prompt_content="Real approved prompt",
            priority=10, admin_approved=True)

    def _get(self, topic, confidence=0.6):
        with mock.patch("researchdata.views.embed_query",
                        return_value=np.zeros(4)), \
             mock.patch("researchdata.views.classify_query",
                        return_value=[(topic.id, confidence)]), \
             mock.patch("researchdata.views.rank_prompts",
                        return_value=[(self.real_prompt.id, confidence)]):
            return self.client.post(
                MATCH_PATH,
                data=urlencode({"user_search_query": "anything"}),
                content_type="application/x-www-form-urlencoded",
                **workshop_headers(),
            ).json()

    # --- promptless topic: this is the branch the flag controls ---

    def test_promptless_topic_serves_nothing_when_flag_off(self):
        set_serve_placeholders(False)
        data = self._get(self.promptless_topic)
        self.assertEqual(data["prompts"], [])
        self.assertIs(data["prompt"], False)

    def test_promptless_topic_serves_placeholder_when_flag_on(self):
        set_serve_placeholders(True)
        data = self._get(self.promptless_topic, confidence=0.612)
        self.assertEqual(len(data["prompts"]), 1)
        entry = data["prompts"][0]
        self.assertEqual(entry["matched_by"], "placeholder")
        self.assertIn("Vaccination", entry["topic"])
        self.assertEqual(entry["topic_id"], self.promptless_topic.id)
        self.assertEqual(entry["confidence"], 0.612)
        # Legacy singular field also surfaces the placeholder.
        self.assertEqual(data["prompt"], entry)

    # --- topic with an approved prompt: unaffected either way ---

    def test_real_prompt_topic_served_when_flag_on(self):
        set_serve_placeholders(True)
        data = self._get(self.real_topic)
        self.assertEqual(len(data["prompts"]), 1)
        entry = data["prompts"][0]
        self.assertEqual(entry["matched_by"], "classifier")
        self.assertEqual(entry["id"], self.real_prompt.id)
        self.assertEqual(entry["prompt_content"], "Real approved prompt")

    def test_real_prompt_topic_served_when_flag_off(self):
        set_serve_placeholders(False)
        data = self._get(self.real_topic)
        self.assertEqual(len(data["prompts"]), 1)
        entry = data["prompts"][0]
        self.assertEqual(entry["matched_by"], "classifier")
        self.assertEqual(entry["id"], self.real_prompt.id)
        self.assertEqual(entry["prompt_content"], "Real approved prompt")

    # --- below-threshold / no match: untouched by this change either way ---

    def test_no_match_serves_nothing_regardless_of_flag(self):
        for flag in (True, False):
            set_serve_placeholders(flag)
            with mock.patch("researchdata.views.embed_query",
                            return_value=np.zeros(4)), \
                 mock.patch("researchdata.views.classify_query",
                            return_value=[]):
                data = self.client.post(
                    MATCH_PATH,
                    data=urlencode({"user_search_query": "weather tomorrow"}),
                    content_type="application/x-www-form-urlencoded",
                    **workshop_headers(),
                ).json()
            self.assertEqual(data["prompts"], [])
            self.assertIs(data["prompt"], False)
