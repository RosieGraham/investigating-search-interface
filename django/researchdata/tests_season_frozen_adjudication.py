"""Frozen adjudication table must exist and must not include quarantined queries."""

import csv
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
FROZEN = REPO_ROOT / "release-evidence" / "season-2026" / "frozen-adjudication.csv"
QUARANTINE = {
    "youtube",
    "how long to boil an egg",
    "what is gerrymandering",
    "skyscanner",
    "right to be forgotten",
    "how to boil an egg",
}


class FrozenAdjudicationTableTests(SimpleTestCase):
    def test_frozen_table_exists_without_quarantined_queries(self):
        self.assertTrue(FROZEN.is_file(), "frozen adjudication table is required before replay")
        with FROZEN.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertGreaterEqual(len(rows), 50)
        queries = {row["query"].strip().lower() for row in rows}
        self.assertTrue(queries.isdisjoint(QUARANTINE), queries & QUARANTINE)
        for row in rows:
            self.assertIn(row["expected_card"], {"yes", "no"})
            self.assertIn(row["max_severity"], {"Blocker", "High", "Medium", "Low"})
