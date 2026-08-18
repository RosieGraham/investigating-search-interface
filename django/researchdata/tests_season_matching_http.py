"""
Failing contract: POST-only matching, media type, fields, size, identity, status.
"""

from unittest import mock
from urllib.parse import urlencode

import numpy as np
from django.test import TestCase

from .models import Prompt, Topic, TopicGroup
from .tests_season_support import (
    EXT_VERSION,
    MATCH_PATH,
    MARKER,
    assert_json_error,
    match_post,
    workshop_headers,
)


class MatchingHttpContractTests(TestCase):
    def setUp(self):
        group = TopicGroup.objects.create(name="G")
        self.topic = Topic.objects.create(topic_group=group, name="T", description="prose")
        self.prompt = Prompt.objects.create(
            topic=self.topic, prompt_content="Real approved prompt",
            priority=10, admin_approved=True)

    def _spy_classify(self):
        return mock.patch.multiple(
            "researchdata.views",
            embed_query=mock.DEFAULT,
            classify_query=mock.DEFAULT,
            rank_prompts=mock.DEFAULT,
        )

    def test_get_is_method_not_allowed_and_does_not_classify(self):
        with self._spy_classify() as mocks:
            mocks["embed_query"].return_value = np.zeros(4)
            mocks["classify_query"].return_value = [(self.topic.id, 0.9)]
            mocks["rank_prompts"].return_value = [(self.prompt.id, 0.9)]
            resp = self.client.get(
                MATCH_PATH,
                {"user_search_query": MARKER},
                **workshop_headers(),
            )
        assert_json_error(resp, 405, "method_not_allowed")
        self.assertEqual(resp.get("Allow"), "POST")
        mocks["classify_query"].assert_not_called()
        mocks["embed_query"].assert_not_called()

    def test_put_patch_delete_are_method_not_allowed(self):
        for method in ("PUT", "PATCH", "DELETE"):
            with self._spy_classify() as mocks:
                mocks["classify_query"].return_value = [(self.topic.id, 0.9)]
                resp = self.client.generic(
                    method,
                    MATCH_PATH,
                    data=urlencode({"user_search_query": MARKER}),
                    content_type="application/x-www-form-urlencoded",
                    **workshop_headers(),
                )
            assert_json_error(resp, 405, "method_not_allowed")
            mocks["classify_query"].assert_not_called()

    def test_json_body_is_unsupported_media_type(self):
        with self._spy_classify() as mocks:
            mocks["classify_query"].return_value = [(self.topic.id, 0.9)]
            resp = self.client.post(
                MATCH_PATH,
                data='{"user_search_query": "%s"}' % MARKER,
                content_type="application/json",
                **workshop_headers(),
            )
        assert_json_error(resp, 415, "unsupported_media_type")
        mocks["classify_query"].assert_not_called()

    def test_urlencoded_with_charset_is_accepted(self):
        with self._spy_classify() as mocks:
            mocks["embed_query"].return_value = np.zeros(4)
            mocks["classify_query"].return_value = [(self.topic.id, 0.6)]
            mocks["rank_prompts"].return_value = [(self.prompt.id, 0.6)]
            resp = match_post(
                self.client,
                "anything",
                content_type="application/x-www-form-urlencoded; charset=UTF-8",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Cache-Control"], "no-store")

    def test_missing_query_field_is_invalid_request(self):
        with self._spy_classify() as mocks:
            mocks["classify_query"].return_value = [(self.topic.id, 0.9)]
            resp = self.client.post(
                MATCH_PATH,
                data="",
                content_type="application/x-www-form-urlencoded",
                **workshop_headers(),
            )
        assert_json_error(resp, 400, "invalid_request")
        mocks["classify_query"].assert_not_called()

    def test_empty_query_is_invalid_request(self):
        with self._spy_classify() as mocks:
            mocks["classify_query"].return_value = [(self.topic.id, 0.9)]
            resp = match_post(self.client, "")
        assert_json_error(resp, 400, "invalid_request")
        mocks["classify_query"].assert_not_called()

    def test_unknown_field_is_invalid_request(self):
        with self._spy_classify() as mocks:
            mocks["classify_query"].return_value = [(self.topic.id, 0.9)]
            resp = match_post(self.client, "anything", extra_fields={"unexpected": "1"})
        assert_json_error(resp, 400, "invalid_request")
        mocks["classify_query"].assert_not_called()

    def test_duplicate_query_field_is_invalid_request(self):
        with self._spy_classify() as mocks:
            mocks["classify_query"].return_value = [(self.topic.id, 0.9)]
            resp = self.client.generic(
                "POST",
                MATCH_PATH,
                data="user_search_query=one&user_search_query=two",
                content_type="application/x-www-form-urlencoded",
                **workshop_headers(),
            )
        assert_json_error(resp, 400, "invalid_request")
        mocks["classify_query"].assert_not_called()

    def test_body_over_8192_bytes_is_too_large(self):
        with self._spy_classify() as mocks:
            mocks["classify_query"].return_value = [(self.topic.id, 0.9)]
            huge = "x" * 8200
            resp = match_post(self.client, huge)
        assert_json_error(resp, 413, "request_too_large")
        mocks["classify_query"].assert_not_called()

    def test_query_over_2048_code_points_is_too_large(self):
        with self._spy_classify() as mocks:
            mocks["classify_query"].return_value = [(self.topic.id, 0.9)]
            resp = match_post(self.client, "é" * 2049)
        assert_json_error(resp, 413, "request_too_large")
        mocks["classify_query"].assert_not_called()

    def test_missing_build_id_is_release_not_allowed(self):
        with self._spy_classify() as mocks:
            mocks["classify_query"].return_value = [(self.topic.id, 0.9)]
            resp = self.client.post(
                MATCH_PATH,
                data=urlencode({"user_search_query": "anything"}),
                content_type="application/x-www-form-urlencoded",
                **{"HTTP_X_ISI_EXTENSION_VERSION": EXT_VERSION},
            )
        assert_json_error(resp, 403, "release_not_allowed")
        mocks["classify_query"].assert_not_called()

    def test_unknown_build_id_is_release_not_allowed(self):
        with self._spy_classify() as mocks:
            mocks["classify_query"].return_value = [(self.topic.id, 0.9)]
            resp = match_post(
                self.client,
                "anything",
                headers=workshop_headers(HTTP_X_ISI_BUILD_ID="forged-workshop-id"),
            )
        assert_json_error(resp, 403, "release_not_allowed")
        mocks["classify_query"].assert_not_called()

    def test_stale_extension_version_is_release_not_allowed(self):
        with self._spy_classify() as mocks:
            mocks["classify_query"].return_value = [(self.topic.id, 0.9)]
            resp = match_post(
                self.client,
                "anything",
                headers=workshop_headers(HTTP_X_ISI_EXTENSION_VERSION="2.1.0"),
            )
        assert_json_error(resp, 403, "release_not_allowed")
        mocks["classify_query"].assert_not_called()

    def test_success_and_silence_preserve_shape_and_no_store(self):
        with self._spy_classify() as mocks:
            mocks["embed_query"].return_value = np.zeros(4)
            mocks["classify_query"].return_value = [(self.topic.id, 0.6)]
            mocks["rank_prompts"].return_value = [(self.prompt.id, 0.6)]
            hit = match_post(self.client, "anything")
            mocks["classify_query"].return_value = []
            silence = match_post(self.client, "weather tomorrow")
        for resp in (hit, silence):
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp["Cache-Control"], "no-store")
            body = resp.json()
            for key in ("prompt", "prompts", "topics", "classifier"):
                self.assertIn(key, body)
        self.assertEqual(len(hit.json()["prompts"]), 1)
        self.assertIn("matched_by", hit.json()["prompts"][0])
        self.assertIn("confidence", hit.json()["prompts"][0])
        self.assertEqual(silence.json()["prompts"], [])
        self.assertIs(silence.json()["prompt"], False)

    def test_exact_path_does_not_redirect(self):
        resp = self.client.post(
            MATCH_PATH,
            data=urlencode({"user_search_query": "anything"}),
            content_type="application/x-www-form-urlencoded",
            follow=False,
            **workshop_headers(),
        )
        self.assertNotIn(resp.status_code, (301, 302, 307, 308))

    def test_debug_endpoint_does_not_accept_query_in_url(self):
        resp = self.client.get("/data/classifier/debug/", {"user_search_query": MARKER})
        self.assertIn(resp.status_code, (403, 404, 405))
        if resp.get("Content-Type", "").startswith("application/json"):
            self.assertNotIn(MARKER, resp.content.decode())
