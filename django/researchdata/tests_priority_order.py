"""
Test for the prompt display-order fix: similarity selects WHICH prompts
appear, editorial priority decides the ORDER they are presented in.

The classifier functions are patched out so the test drives the view's
ordering logic directly, without ONNX.
"""

from unittest import mock

import numpy as np
from django.test import TestCase

from .models import Prompt, Topic, TopicGroup


class PriorityDisplayOrderTests(TestCase):
    def setUp(self):
        group = TopicGroup.objects.create(name="G")
        self.topic = Topic.objects.create(topic_group=group, name="T",
                                          description="prose")
        self.low = Prompt.objects.create(
            topic=self.topic, prompt_content="Activity prompt",
            priority=10, admin_approved=True)
        self.high = Prompt.objects.create(
            topic=self.topic, prompt_content="Mechanism prompt",
            priority=40, admin_approved=True)
        self.mid = Prompt.objects.create(
            topic=self.topic, prompt_content="Critical question",
            priority=20, admin_approved=True)
        self.none_pri = Prompt.objects.create(
            topic=self.topic, prompt_content="Watch out",
            priority=None, admin_approved=True)

    def _get(self):
        # Similarity order deliberately puts the LOW priority prompt first:
        # if the view still displays in similarity order, the test fails.
        sim_order = [(self.low.id, 0.9), (self.none_pri.id, 0.8),
                     (self.mid.id, 0.7), (self.high.id, 0.6)]
        with mock.patch("researchdata.views.embed_query",
                        return_value=np.zeros(4)), \
             mock.patch("researchdata.views.classify_query",
                        return_value=[(self.topic.id, 0.6)]), \
             mock.patch("researchdata.views.rank_prompts",
                        return_value=sim_order):
            return self.client.get("/data/prompt/get/",
                                   {"user_search_query": "anything"}).json()

    def test_display_order_is_priority_not_similarity(self):
        data = self._get()
        contents = [p["prompt_content"] for p in data["prompts"]]
        self.assertEqual(contents, [
            "Mechanism prompt",      # priority 40
            "Critical question",     # priority 20
            "Activity prompt",       # priority 10
            "Watch out",             # priority None -> 0, last
        ])

    def test_first_prompt_field_matches_new_order(self):
        data = self._get()
        self.assertEqual(data["prompt"]["prompt_content"], "Mechanism prompt")

    def test_order_is_deterministic_via_id_tiebreak(self):
        self.none_pri.priority = 40
        self.none_pri.save()
        data = self._get()
        contents = [p["prompt_content"] for p in data["prompts"]]
        # Equal priority 40: lower id (Mechanism was created before Watch out)
        self.assertEqual(contents[0], "Mechanism prompt")
        self.assertEqual(contents[1], "Watch out")
