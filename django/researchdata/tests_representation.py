"""
Tests for the Phase 3 representation change: blob splitting, blended
multi-vector scoring, and the margin rule. No ONNX required: index arrays
are injected directly and query vectors passed pre-computed.
"""

import numpy as np
from django.test import TestCase

from . import embedding
from .classifier_config import (
    set_classifier_blend_alpha,
    set_classifier_margin,
    set_classifier_threshold,
)
from .description_blob import recombine_blob, split_blob
from .models import Topic, TopicGroup
from .services.content_apply import apply_package


class SplitBlobTests(TestCase):
    BLOB = ("This content covers cookies: what they store and why sites ask. "
            "Example queries: what are cookies, why does every site ask about cookies. "
            "Distinct from Dark patterns (consent design tricks).")

    def test_round_trip_is_byte_exact(self):
        prose, queries, contrasts = split_blob(self.BLOB)
        self.assertEqual(recombine_blob(prose, queries, contrasts), self.BLOB)

    def test_parts_are_separated(self):
        prose, queries, contrasts = split_blob(self.BLOB)
        self.assertTrue(prose.startswith("This content covers cookies"))
        self.assertNotIn("Example queries", prose)
        self.assertEqual(queries, ["what are cookies",
                                   "why does every site ask about cookies"])
        self.assertTrue(contrasts.startswith("Distinct from Dark patterns"))

    def test_prose_only_blob(self):
        prose, queries, contrasts = split_blob("Just prose, nothing else.")
        self.assertEqual(prose, "Just prose, nothing else.")
        self.assertEqual(queries, [])
        self.assertEqual(contrasts, "")

    def test_empty(self):
        self.assertEqual(split_blob(""), ("", [], ""))
        self.assertEqual(split_blob(None), ("", [], ""))


class BlendedScoringTests(TestCase):
    """Inject a tiny index: two topics, one with an example-query row."""

    def setUp(self):
        group = TopicGroup.objects.create(name="G")
        self.a = Topic.objects.create(topic_group=group, name="Topic A",
                                      description="prose a",
                                      example_queries=["query a"])
        self.b = Topic.objects.create(topic_group=group, name="Topic B",
                                      description="prose b")
        # Rows: A-desc, A-example, B-desc. Orthogonal unit vectors so
        # cosine equals the vector component we set.
        matrix = np.zeros((3, 4), dtype=np.float32)
        matrix[0, 0] = 1.0   # A desc
        matrix[1, 1] = 1.0   # A example
        matrix[2, 2] = 1.0   # B desc
        self._saved = (embedding._index_matrix, embedding._index_topic_ids,
                       embedding._row_topic_pos, embedding._row_is_example,
                       embedding._index_dirty)
        embedding._index_matrix = matrix
        embedding._index_topic_ids = [self.a.id, self.b.id]
        embedding._row_topic_pos = np.array([0, 0, 1])
        embedding._row_is_example = np.array([False, True, False])
        embedding._index_dirty = False
        set_classifier_blend_alpha(0.4)
        set_classifier_threshold(0.2)
        set_classifier_margin(0.0)

    def tearDown(self):
        (embedding._index_matrix, embedding._index_topic_ids,
         embedding._row_topic_pos, embedding._row_is_example,
         embedding._index_dirty) = self._saved

    def query(self, desc_a=0.0, ex_a=0.0, desc_b=0.0):
        v = np.zeros(4, dtype=np.float32)
        v[0], v[1], v[2] = desc_a, ex_a, desc_b
        return v

    def test_blend_weights_description_and_best_example(self):
        # cos(desc_a)=0.5, cos(ex_a)=0.9 -> 0.4*0.5 + 0.6*0.9 = 0.74
        scores, ids = embedding._blended_topic_scores(
            self.query(desc_a=0.5, ex_a=0.9))
        self.assertAlmostEqual(float(scores[ids.index(self.a.id)]), 0.74, places=5)

    def test_topic_without_examples_scores_on_description(self):
        scores, ids = embedding._blended_topic_scores(self.query(desc_b=0.8))
        self.assertAlmostEqual(float(scores[ids.index(self.b.id)]), 0.8, places=5)

    def test_example_alone_cannot_dominate(self):
        # Perfect example match with zero desc support: 0.6 * 1.0 = 0.6,
        # not 1.0. The anchor is the point of the blend.
        scores, ids = embedding._blended_topic_scores(self.query(ex_a=1.0))
        self.assertAlmostEqual(float(scores[ids.index(self.a.id)]), 0.6, places=5)

    def test_classify_query_margin_abstains(self):
        set_classifier_margin(0.2)
        # A blended: 0.4*0.6+0.6*0.0 = 0.24; B: 0.3. Gap 0.06 < 0.2 -> [].
        out = embedding.classify_query(
            "anything", _query_vec=self.query(desc_a=0.6, desc_b=0.3))
        self.assertEqual(out, [])

    def test_classify_query_margin_zero_serves(self):
        out = embedding.classify_query(
            "anything", _query_vec=self.query(desc_a=0.6, desc_b=0.3))
        self.assertEqual(out[0][0], self.b.id if 0.3 > 0.24 else self.a.id)
        self.assertEqual(len(out), 2)


class ApplySplitsBlobTests(TestCase):
    def test_apply_package_writes_split_fields(self):
        package = {
            "groups": [{"name": "G"}],
            "topics": [{
                "name": "Cookies",
                "group": "G",
                "description": ("Prose about cookies. "
                                "Example queries: what are cookies. "
                                "Distinct from Dark patterns (tricks)."),
                "example_queries": ["what are cookies", "cookie consent banner"],
                "admin_notes": "",
            }],
            "prompts": [],
        }
        apply_package(package, dry_run=False)
        topic = Topic.objects.get(name="Cookies")
        self.assertEqual(topic.description, "Prose about cookies.")
        # The explicit package list wins over blob-parsed queries.
        self.assertEqual(topic.example_queries,
                         ["what are cookies", "cookie consent banner"])
        self.assertTrue(topic.contrasts.startswith("Distinct from Dark patterns"))
        self.assertNotIn("Example queries", topic.description)
