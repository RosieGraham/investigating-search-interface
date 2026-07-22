"""
Tests for prompt_get accepting POST (privacy: query out of URL logs) and
Cache-Control: no-store on the response.
"""

from unittest import mock

import numpy as np
from django.core.cache import cache
from django.test import TestCase

from .models import Prompt, Topic, TopicGroup


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

    def test_post_returns_same_shape_as_get(self):
        with self._patched() as mocks:
            mocks["embed_query"].return_value = np.zeros(4)
            mocks["classify_query"].return_value = [(self.topic.id, 0.6)]
            mocks["rank_prompts"].return_value = [(self.prompt.id, 0.6)]
            get_data = self.client.get(
                "/data/prompt/get/", {"user_search_query": "anything"}
            ).json()
            post_resp = self.client.post(
                "/data/prompt/get/", {"user_search_query": "anything"}
            )
            post_data = post_resp.json()

        self.assertEqual(get_data["classifier"], post_data["classifier"])
        self.assertEqual(len(get_data["prompts"]), 1)
        self.assertEqual(len(post_data["prompts"]), 1)
        self.assertEqual(get_data["prompts"][0]["id"], post_data["prompts"][0]["id"])
        self.assertEqual(get_data["prompts"][0]["matched_by"], "classifier")
        self.assertEqual(post_data["prompts"][0]["matched_by"], "classifier")

    def test_post_response_has_cache_control_no_store(self):
        with self._patched() as mocks:
            mocks["embed_query"].return_value = np.zeros(4)
            mocks["classify_query"].return_value = [(self.topic.id, 0.6)]
            mocks["rank_prompts"].return_value = [(self.prompt.id, 0.6)]
            resp = self.client.post(
                "/data/prompt/get/", {"user_search_query": "anything"}
            )
        self.assertEqual(resp["Cache-Control"], "no-store")

    def test_get_response_has_cache_control_no_store(self):
        with self._patched() as mocks:
            mocks["embed_query"].return_value = np.zeros(4)
            mocks["classify_query"].return_value = []
            resp = self.client.get(
                "/data/prompt/get/", {"user_search_query": "weather tomorrow"}
            )
        self.assertEqual(resp["Cache-Control"], "no-store")

    def test_post_topics_exclude_parsed(self):
        other_group = TopicGroup.objects.create(name="Other")
        with self._patched() as mocks:
            mocks["embed_query"].return_value = np.zeros(4)
            mocks["classify_query"].return_value = [(self.topic.id, 0.6)]
            mocks["rank_prompts"].return_value = [(self.prompt.id, 0.6)]
            data = self.client.post(
                "/data/prompt/get/",
                {
                    "user_search_query": "anything",
                    "topics_exclude": str(other_group.id),
                },
            ).json()
        # Excluding an unrelated group must not drop this topic's prompt.
        self.assertEqual(len(data["prompts"]), 1)
        self.assertEqual(data["prompts"][0]["id"], self.prompt.id)
