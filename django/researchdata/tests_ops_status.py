"""
Tests for the /data/ops/status/ endpoint. The classifier is patched so the
tests exercise the endpoint's structure and canary logic without ONNX.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Prompt, Topic, TopicGroup

User = get_user_model()


class OpsStatusTests(TestCase):
    def setUp(self):
        group = TopicGroup.objects.create(name="G")
        self.seo = Topic.objects.create(
            topic_group=group, name="Search engine optimisation",
            description="prose", example_queries=["what is SEO"])
        self.conc = Topic.objects.create(
            topic_group=group, name="Concentration and defaults",
            description="prose")
        Prompt.objects.create(topic=self.seo, prompt_content="p",
                              admin_approved=True)
        staff = User.objects.create_user(
            username="ops@test.com", email="ops@test.com", password="pw-test-1234",
            is_staff=True,
        )
        self.client.force_login(staff)

    def _get(self, classify_side_effect):
        with mock.patch("researchdata.views.classify_query",
                        side_effect=classify_side_effect):
            return self.client.get("/data/ops/status/").json()

    def test_status_shape_and_no_secrets(self):
        def fake(query, threshold=None, top_k=None, margin=None, _query_vec=None):
            if query == "what is SEO":
                return [(self.seo.id, 0.51)]
            if query == "why does everyone use Google":
                return [(self.conc.id, 0.46)]
            return [(self.seo.id, 0.2)]
        data = self._get(fake)
        for key in ("classifier", "content", "config", "canaries",
                    "canaries_pass", "database", "git_commit"):
            self.assertIn(key, data)
        self.assertEqual(data["database"], "ok")
        self.assertEqual(data["content"]["topics"], 2)
        self.assertEqual(data["content"]["approved_prompts"], 1)
        # Nothing that looks like a secret or participant data.
        text = str(data)
        self.assertNotIn("postgres://", text)
        self.assertNotIn("SECRET", text)

    def test_canaries_pass_when_behaviour_is_expected(self):
        def fake(query, threshold=None, top_k=None, margin=None, _query_vec=None):
            if query == "what is SEO":
                return [(self.seo.id, 0.51)]
            if query == "why does everyone use Google":
                return [(self.conc.id, 0.46)]
            return [(self.seo.id, 0.20)]  # weather: below threshold
        data = self._get(fake)
        self.assertTrue(data["canaries_pass"])

    def test_canary_fails_when_wrong_topic_wins(self):
        def fake(query, threshold=None, top_k=None, margin=None, _query_vec=None):
            return [(self.conc.id, 0.51)]  # everything lands on the wrong topic
        data = self._get(fake)
        self.assertFalse(data["canaries_pass"])
        seo_row = next(c for c in data["canaries"] if c["query"] == "what is SEO")
        self.assertFalse(seo_row["pass"])

    def test_canary_fails_when_negative_control_fires(self):
        def fake(query, threshold=None, top_k=None, margin=None, _query_vec=None):
            if query == "weather tomorrow":
                return [(self.seo.id, 0.50)]  # above threshold: bad
            if query == "what is SEO":
                return [(self.seo.id, 0.51)]
            return [(self.conc.id, 0.46)]
        data = self._get(fake)
        self.assertFalse(data["canaries_pass"])

    def test_classifier_status_is_captured_after_canaries(self):
        # On a freshly woken worker the canaries are what load the index, so
        # the classifier block must be read after they run. Simulated here: a
        # status that reports 0 rows until the first classify_query call.
        state = {"warmed": False}

        def fake_classify(query, threshold=None, top_k=None, margin=None, _query_vec=None):
            state["warmed"] = True
            if query == "what is SEO":
                return [(self.seo.id, 0.51)]
            if query == "why does everyone use Google":
                return [(self.conc.id, 0.46)]
            return [(self.seo.id, 0.20)]

        def fake_status():
            return {"index_rows": 228 if state["warmed"] else 0}

        with mock.patch("researchdata.views.classify_query", side_effect=fake_classify), \
                mock.patch("researchdata.embedding.classifier_status", side_effect=fake_status):
            data = self.client.get("/data/ops/status/").json()
        self.assertEqual(data["classifier"]["index_rows"], 228)
