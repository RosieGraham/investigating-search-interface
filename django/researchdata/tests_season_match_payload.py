"""Public match response must not leak editorial notes or query text in logs."""

import logging
import traceback
from unittest import mock
from urllib.parse import urlencode

import numpy as np
from django.forms.models import model_to_dict
from django.test import TestCase

from .external_links import sanitise_learn_more_url
from .models import Prompt, Topic, TopicGroup
from .tests_season_support import MARKER, MATCH_PATH, match_post, workshop_headers


class TopicPayloadContractTests(TestCase):
    def setUp(self):
        self.group = TopicGroup.objects.create(
            name="PublicGroup",
            admin_notes=f"editorial {MARKER} never for participants",
        )
        self.topic = Topic.objects.create(
            topic_group=self.group, name="T", description="prose")
        self.prompt = Prompt.objects.create(
            topic=self.topic, prompt_content="Approved",
            priority=10, admin_approved=True)

    def _patched(self):
        return mock.patch.multiple(
            "researchdata.views",
            embed_query=mock.DEFAULT,
            classify_query=mock.DEFAULT,
            rank_prompts=mock.DEFAULT,
        )

    def test_admin_notes_are_absent_from_match_topics(self):
        with self._patched() as mocks:
            mocks["embed_query"].return_value = np.zeros(4)
            mocks["classify_query"].return_value = [(self.topic.id, 0.6)]
            mocks["rank_prompts"].return_value = [(self.prompt.id, 0.6)]
            resp = match_post(self.client, "anything")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertNotIn(MARKER, body)
        topics = resp.json()["topics"]
        self.assertEqual(len(topics), 1)
        self.assertEqual(set(topics[0].keys()), {"id", "name", "excluded"})
        self.assertEqual(topics[0]["id"], self.group.id)
        self.assertEqual(topics[0]["name"], "PublicGroup")
        self.assertNotIn("admin_notes", topics[0])
        self.assertNotIn("meta_created_datetime", topics[0])
        self.assertNotIn("meta_lastupdated_datetime", topics[0])

        leaked = {**model_to_dict(self.group), "excluded": 0}
        self.assertIn(MARKER, leaked["admin_notes"])
        with self.assertRaises(AssertionError):
            self.assertNotIn(MARKER, leaked["admin_notes"])


class ClassifierLogContractTests(TestCase):
    def setUp(self):
        group = TopicGroup.objects.create(name="G")
        self.topic = Topic.objects.create(topic_group=group, name="T", description="prose")
        Prompt.objects.create(
            topic=self.topic, prompt_content="Approved",
            priority=10, admin_approved=True)

    def _capture_logs(self):
        records = []

        class Handler(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = Handler()
        handler.setLevel(logging.DEBUG)
        root = logging.getLogger()
        research = logging.getLogger("researchdata")
        django = logging.getLogger("django")
        root.addHandler(handler)
        research.addHandler(handler)
        django.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(research.removeHandler, handler)
        self.addCleanup(django.removeHandler, handler)
        return records

    def _text(self, record):
        parts = [record.getMessage()]
        if record.exc_info:
            parts.append("".join(traceback.format_exception(*record.exc_info)))
        if record.exc_text:
            parts.append(record.exc_text)
        return "\n".join(parts)

    def _assert_marker_absent(self, records):
        for record in records:
            text = self._text(record)
            if MARKER in text:
                self.fail(f"query marker reached logger {record.name}: {text}")

    def test_classifier_exception_does_not_log_query_text(self):
        records = self._capture_logs()
        planted = RuntimeError(f"planted matcher failure for {MARKER}")
        with mock.patch("researchdata.views.embed_query", side_effect=planted):
            resp = match_post(self.client, MARKER)
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(MARKER, resp.content.decode("utf-8"))
        self._assert_marker_absent(records)
        self.assertTrue(
            any("classifier_error code=CLS-500" in r.getMessage() for r in records),
            "expected a stable CLS-500 log line",
        )

    def test_contract_failures_do_not_log_query_text(self):
        records = self._capture_logs()
        paths = [
            lambda: self.client.get(
                MATCH_PATH, {"user_search_query": MARKER}, **workshop_headers()),
            lambda: self.client.post(
                MATCH_PATH,
                data='{"user_search_query": "%s"}' % MARKER,
                content_type="application/json",
                **workshop_headers(),
            ),
            lambda: self.client.post(
                MATCH_PATH,
                data=urlencode({"user_search_query": MARKER, "nope": "1"}),
                content_type="application/x-www-form-urlencoded",
                **workshop_headers(),
            ),
            lambda: self.client.post(
                MATCH_PATH,
                data=urlencode({"user_search_query": MARKER}),
                content_type="application/x-www-form-urlencoded",
                HTTP_X_ISI_BUILD_ID="forged",
                HTTP_X_ISI_EXTENSION_VERSION="0.2.1",
            ),
        ]
        for call in paths:
            call()
        self._assert_marker_absent(records)

    def test_inverted_logger_exception_would_carry_the_marker(self):
        logger = logging.getLogger("researchdata")
        records = []

        class Handler(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = Handler()
        logger.addHandler(handler)
        try:
            try:
                raise RuntimeError(f"planted matcher failure for {MARKER}")
            except Exception:
                logger.exception("Classifier error; falling back to triggers.")
        finally:
            logger.removeHandler(handler)
        leaked = any(MARKER in self._text(r) for r in records)
        self.assertTrue(leaked, "inverted control must observe the marker in logger.exception")
        with self.assertRaises(AssertionError):
            if leaked:
                raise AssertionError("marker present")


class LearnMoreUrlTests(TestCase):
    def test_only_approved_https_origins_survive(self):
        allowed = "https://investigating-search-interface.onrender.com/seeed/x"
        self.assertEqual(sanitise_learn_more_url(allowed), allowed)
        self.assertIsNone(sanitise_learn_more_url("javascript:alert(1)"))
        self.assertIsNone(sanitise_learn_more_url("https://evil.example/collect"))
        self.assertIsNone(sanitise_learn_more_url("http://investigating-search-interface.onrender.com/x"))

    def test_prompt_save_strips_hostile_seeed_url(self):
        group = TopicGroup.objects.create(name="G")
        topic = Topic.objects.create(topic_group=group, name="T", description="prose")
        prompt = Prompt.objects.create(
            topic=topic, prompt_content="Approved", admin_approved=True,
            seeed_url="https://evil.example/collect",
        )
        prompt.refresh_from_db()
        self.assertIsNone(prompt.seeed_url)
