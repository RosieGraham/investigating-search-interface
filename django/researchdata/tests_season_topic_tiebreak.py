"""
Failing contract: explicit deterministic topic-level tie-break (amendment 3).
"""

import inspect

import numpy as np
from django.test import SimpleTestCase

from . import embedding


class TopicTieBreakTests(SimpleTestCase):
    def test_select_ranked_topic_ids_exists_and_is_stable(self):
        try:
            from researchdata.embedding import select_ranked_topic_ids
        except ImportError as exc:
            self.fail(f"select_ranked_topic_ids is required for topic ties: {exc}")
        scores = np.array([0.41, 0.41], dtype=np.float32)
        topic_ids = np.array([20, 10])
        first = None
        for _ in range(20):
            ordered = select_ranked_topic_ids(scores, topic_ids)
            self.assertEqual(list(ordered), list(ordered))
            if first is None:
                first = list(ordered)
            else:
                self.assertEqual(list(ordered), first)
        self.assertEqual(int(first[0]), 10)

    def test_classify_query_source_has_explicit_tie_break(self):
        src = inspect.getsource(embedding.classify_query)
        self.assertNotRegex(
            src,
            r"order = np\.argsort\(-scores\)\s*$",
            msg="bare np.argsort(-scores) is not a deterministic topic tie-break",
        )
        has_explicit = (
            "select_ranked_topic_ids" in src
            or "lexsort" in src
        )
        self.assertTrue(has_explicit, "classify_query must call the explicit topic tie-break")

    def test_production_ranker_uses_the_same_topic_tie_break(self):
        from researchdata import evaluation

        src = inspect.getsource(evaluation.production_ranker)
        self.assertIn("select_ranked_topic_ids", src)
        self.assertNotIn("np.argsort(-scores)", src)
