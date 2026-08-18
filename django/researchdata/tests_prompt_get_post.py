"""
Tests for prompt_get POST matching (workshop contract) and Cache-Control.
"""

from unittest import mock
from urllib.parse import urlencode

import numpy as np
from django.core.cache import cache
from django.test import TestCase

from .models import Prompt, Topic, TopicGroup
from .tests_season_support import MATCH_PATH, workshop_headers


class PromptGetMethodTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

        group = TopicGroup.objects.create(name="G")
        self.topic = Topic.objects.create(
            topic_group=group, name="Concentration and defaults", description="prose")
        self.prompt = Prompt.objects.create(
            topic=self.topic, prompt_content="Real approved prompt",
            priority=10, admin_approved=True)

    def _patched(self):
        return mock.patch.multiple(
            "researchdata.views",
            embed_query=mock.DEFAULT,
            classify_query=mock.DEFAULT,
            rank_prompts=mock.DEFAULT,
        )

    def _post(self, fields, **extra):
        return self.client.post(
            MATCH_PATH,
            data=urlencode(fields),
            content_type="application/x-www-form-urlencoded",
            **workshop_headers(),
            **extra,
        )

    def test_post_returns_prompt_shape(self):
        with self._patched() as mocks:
            mocks["embed_query"].return_value = np.zeros(4)
            mocks["classify_query"].return_value = [(self.topic.id, 0.6)]
            mocks["rank_prompts"].return_value = [(self.prompt.id, 0.6)]
            post_resp = self._post({"user_search_query": "anything"})
            post_data = post_resp.json()

        self.assertEqual(post_resp.status_code, 200)
        self.assertEqual(len(post_data["prompts"]), 1)
        self.assertEqual(post_data["prompts"][0]["id"], self.prompt.id)
        self.assertEqual(post_data["prompts"][0]["matched_by"], "classifier")

    def test_post_response_has_cache_control_no_store(self):
        with self._patched() as mocks:
            mocks["embed_query"].return_value = np.zeros(4)
            mocks["classify_query"].return_value = [(self.topic.id, 0.6)]
            mocks["rank_prompts"].return_value = [(self.prompt.id, 0.6)]
            resp = self._post({"user_search_query": "anything"})
        self.assertEqual(resp["Cache-Control"], "no-store")

    def test_get_is_method_not_allowed_with_no_store(self):
        with self._patched() as mocks:
            mocks["embed_query"].return_value = np.zeros(4)
            mocks["classify_query"].return_value = []
            resp = self.client.get(
                MATCH_PATH,
                {"user_search_query": "weather tomorrow"},
                **workshop_headers(),
            )
        self.assertEqual(resp.status_code, 405)
        self.assertEqual(resp["Cache-Control"], "no-store")
        mocks["classify_query"].assert_not_called()

    def test_post_topics_exclude_parsed(self):
        other_group = TopicGroup.objects.create(name="Other")
        with self._patched() as mocks:
            mocks["embed_query"].return_value = np.zeros(4)
            mocks["classify_query"].return_value = [(self.topic.id, 0.6)]
            mocks["rank_prompts"].return_value = [(self.prompt.id, 0.6)]
            data = self._post({
                "user_search_query": "anything",
                "topics_exclude": str(other_group.id),
            }).json()
        # Excluding an unrelated group must not drop this topic's prompt.
        self.assertEqual(len(data["prompts"]), 1)
        self.assertEqual(data["prompts"][0]["id"], self.prompt.id)
