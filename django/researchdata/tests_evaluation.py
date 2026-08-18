"""
Tests for the matching evaluation harness (Phase 1 of the matching brief).

All tests inject a stub ranker so nothing here needs the ONNX model. The
decision logic, scoring semantics and CSV handling are what is under test;
the encoder is tested by the reproduction checks in the matching report.
"""

import tempfile
from pathlib import Path

from django.test import TestCase

from .evaluation import LabelledRow, decide, evaluate, load_labelled_csv
from .models import Prompt, Topic, TopicGroup


def make_topic(group, name, description="", with_prompt=True, approved=True):
    topic = Topic.objects.create(
        topic_group=group, name=name, description=description or None)
    if with_prompt:
        Prompt.objects.create(
            topic=topic, prompt_content=f"Prompt for {name}",
            admin_approved=approved)
    return topic


class DecideTests(TestCase):
    def test_below_threshold_abstains(self):
        served, note = decide([(1, 0.30), (2, 0.20)], 0.35, 0.0, 3, {1, 2})
        self.assertIsNone(served)
        self.assertEqual(note, "below-threshold")

    def test_inside_margin_abstains(self):
        served, note = decide([(1, 0.50), (2, 0.499)], 0.35, 0.02, 3, {1, 2})
        self.assertIsNone(served)
        self.assertEqual(note, "inside-margin")

    def test_margin_zero_preserves_current_behaviour(self):
        served, note = decide([(1, 0.50), (2, 0.4999)], 0.35, 0.0, 3, {1, 2})
        self.assertEqual(served, 1)

    def test_promptless_top_does_not_fall_through_to_prompt_rich_second(self):
        served, note = decide([(1, 0.60), (2, 0.40)], 0.35, 0.0, 3, {2})
        self.assertIsNone(served)
        self.assertEqual(note, "no-approved-prompts")

    def test_no_fall_through_below_threshold(self):
        served, note = decide([(1, 0.60), (2, 0.30)], 0.35, 0.0, 3, {2})
        self.assertIsNone(served)
        self.assertEqual(note, "no-approved-prompts")

    def test_later_serveable_topic_is_not_used(self):
        ranked = [(1, 0.60), (2, 0.55), (3, 0.50), (4, 0.45)]
        served, note = decide(ranked, 0.35, 0.0, 3, {4})
        self.assertIsNone(served)
        self.assertEqual(note, "no-approved-prompts")


class EvaluateTests(TestCase):
    def setUp(self):
        group = TopicGroup.objects.create(name="Test group")
        self.covered = make_topic(group, "Covered topic")
        self.empty = make_topic(group, "Empty topic", with_prompt=False)
        self.other = make_topic(group, "Other topic")
        self.scores = {}

    def ranker(self, query):
        return self.scores[query]

    def test_correct_card_and_metrics(self):
        self.scores["q1"] = [(self.covered.id, 0.6), (self.other.id, 0.3)]
        rows = [LabelledRow("q1", "Covered topic", True, "cat-a")]
        out = evaluate(rows, threshold=0.35, ranker=self.ranker)
        m = out["metrics"]
        self.assertEqual(m["accuracy_at_1"], 1.0)
        self.assertEqual(m["coverage"], 1.0)
        self.assertEqual(m["mrr"], 1.0)
        self.assertEqual(out["rows"][0]["outcome"], "correct-card")

    def test_expected_topic_without_prompts_scores_silence_correct(self):
        # The "are vaccines safe" shape: right topic, nothing approved to
        # say, silence is the correct outcome.
        self.scores["q2"] = [(self.empty.id, 0.65), (self.other.id, 0.30)]
        rows = [LabelledRow("q2", "Empty topic", False, "ordinary-no-prompts")]
        out = evaluate(rows, threshold=0.35, ranker=self.ranker)
        self.assertEqual(out["metrics"]["no_card_expected_correct"], 1.0)
        self.assertTrue(out["rows"][0]["correct"])

    def test_promptless_top_is_correct_silence_not_a_lower_topic_card(self):
        self.scores["q3"] = [(self.empty.id, 0.65), (self.other.id, 0.40)]
        rows = [LabelledRow("q3", "Empty topic", False, "ordinary-no-prompts")]
        out = evaluate(rows, threshold=0.35, ranker=self.ranker)
        self.assertTrue(out["rows"][0]["correct"])
        self.assertEqual(out["rows"][0]["served_topic"], "")
        self.assertEqual(out["rows"][0]["outcome"], "correct-silence")

    def test_negative_control_false_positive_counted(self):
        self.scores["gmail login"] = [(self.covered.id, 0.5), (self.other.id, 0.2)]
        rows = [LabelledRow("gmail login", "NONE", False, "negative-control")]
        out = evaluate(rows, threshold=0.35, ranker=self.ranker)
        self.assertEqual(out["metrics"]["false_positive_negative_controls"], 1.0)
        self.assertEqual(out["rows"][0]["outcome"], "false-positive")

    def test_negative_control_silence_correct(self):
        self.scores["weather"] = [(self.covered.id, 0.2), (self.other.id, 0.1)]
        rows = [LabelledRow("weather", "NONE", False, "negative-control")]
        out = evaluate(rows, threshold=0.35, ranker=self.ranker)
        self.assertEqual(out["metrics"]["false_positive_negative_controls"], 0.0)

    def test_expected_topic_absent_from_database_does_not_crash(self):
        self.scores["q4"] = [(self.covered.id, 0.6), (self.other.id, 0.3)]
        rows = [LabelledRow("q4", "Topic that does not exist", True, "cat")]
        out = evaluate(rows, threshold=0.35, ranker=self.ranker)
        self.assertEqual(out["metrics"]["accuracy_at_1"], 0.0)
        self.assertEqual(out["rows"][0]["expected_rank"], 0)

    def test_margin_abstention_recorded(self):
        self.scores["q5"] = [(self.covered.id, 0.50), (self.other.id, 0.495)]
        rows = [LabelledRow("q5", "Covered topic", True, "cat")]
        out = evaluate(rows, threshold=0.35, margin=0.02, ranker=self.ranker)
        self.assertEqual(out["metrics"]["coverage"], 0.0)
        self.assertIn("inside-margin", out["rows"][0]["outcome"])
        # Raw top-1 was right even though the rule abstained; both facts
        # must be visible.
        self.assertEqual(out["metrics"]["accuracy_at_1_raw"], 1.0)


class LoadCsvTests(TestCase):
    def _write(self, text):
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8")
        tmp.write(text)
        tmp.close()
        return Path(tmp.name)

    def test_loads_standard_columns(self):
        path = self._write(
            "query,expected_topic,expected_card,category\n"
            "what is SEO,Search engine optimisation,yes,collision-seo\n"
            "gmail login,NONE,no,negative-control\n")
        rows = load_labelled_csv(path)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0].expected_card)
        self.assertFalse(rows[1].expected_card)
        self.assertEqual(rows[1].expected_topic, "NONE")

    def test_absorbs_query_shape_column(self):
        path = self._write(
            "query,expected_topic,expected_card,category,query_shape\n"
            "what is SEO,SEO,yes,seo,ordinary\n")
        rows = load_labelled_csv(path)
        self.assertEqual(rows[0].query_shape, "ordinary")

    def test_missing_required_column_raises(self):
        path = self._write("query,expected_topic\nx,y\n")
        with self.assertRaises(ValueError):
            load_labelled_csv(path)

    def test_extra_unknown_columns_ignored(self):
        path = self._write(
            "query,expected_topic,expected_card,category,labeller,confidence\n"
            "q,NONE,no,neg,emilia,high\n")
        rows = load_labelled_csv(path)
        self.assertEqual(len(rows), 1)
