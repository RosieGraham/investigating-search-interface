"""
Run the matching evaluation harness against a labelled query CSV.

    python django/manage.py evaluate_matching --file data/labelled-queries-2026-07-09.csv
    python django/manage.py evaluate_matching --threshold 0.35 --margin 0.02
    python django/manage.py evaluate_matching --csv out.csv --json out.json

Offline, against the local database, no HTTP. Persists every run to
MatchingEvaluation (use --no-persist for scratch runs) so results form a
time series with full provenance.
"""

import csv as csv_module
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from researchdata import evaluation
from researchdata.classifier_config import get_classifier_threshold
from researchdata.models import MatchingEvaluation

DEFAULT_FILE = Path(__file__).resolve().parents[4] / "data" / "labelled-queries-2026-07-09.csv"


class Command(BaseCommand):
    help = "Evaluate matching quality against a labelled query set."

    def add_arguments(self, parser):
        parser.add_argument("--file", default=str(DEFAULT_FILE),
                            help="Labelled CSV (query, expected_topic, expected_card, category[, query_shape]).")
        parser.add_argument("--threshold", type=float, default=None,
                            help="Decision threshold. Default: the live configured value.")
        parser.add_argument("--margin", type=float, default=0.0,
                            help="Required top1 minus top2 margin. Default 0 (current production behaviour).")
        parser.add_argument("--top-k", type=int, default=None,
                            help="Topics considered for serving. Default: settings.CLASSIFIER_TOP_K.")
        parser.add_argument("--csv", dest="csv_out", default=None, help="Write per-row results to this CSV.")
        parser.add_argument("--json", dest="json_out", default=None, help="Write the full result object to this JSON.")
        parser.add_argument("--note", default="", help="Free-text note stored with the persisted run.")
        parser.add_argument("--scorer", default="production", help="Label for the ranking function in use.")
        parser.add_argument("--no-persist", action="store_true", help="Do not save a MatchingEvaluation row.")

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"Labelled file not found: {path}")

        rows = evaluation.load_labelled_csv(path)
        if not rows:
            raise CommandError("Labelled file contains no usable rows.")

        threshold = options["threshold"]
        if threshold is None:
            threshold = get_classifier_threshold()
        top_k = options["top_k"] or settings.CLASSIFIER_TOP_K

        try:
            result = evaluation.evaluate(
                rows, threshold=threshold, margin=options["margin"], top_k=top_k)
        except Exception as exc:
            raise CommandError(f"Evaluation failed: {exc}")

        metrics = result["metrics"]

        if not options["no_persist"]:
            record = MatchingEvaluation.objects.create(
                git_sha=evaluation.git_sha(),
                model_id=settings.EMBEDDING_MODEL_ID,
                model_artifact=evaluation.model_artifact_fingerprint(),
                runtime=evaluation.runtime_descriptor(),
                index_fingerprint=evaluation.current_index_fingerprint(),
                labelled_file=path.name,
                scorer=options["scorer"],
                threshold=threshold,
                margin=options["margin"],
                metrics=metrics,
                results=result["rows"],
                notes=options["note"],
            )
            self.stdout.write(f"Persisted as MatchingEvaluation #{record.id}.")

        self.stdout.write(self.style.MIGRATE_HEADING("Matching evaluation"))
        ordered = [
            "n_rows", "n_expected_card", "accuracy_at_1", "accuracy_at_1_raw",
            "coverage", "false_positive_negative_controls",
            "false_positive_ordinary_uncovered", "no_card_expected_correct",
            "abstention_rate", "mrr", "threshold", "margin", "top_k",
        ]
        for key in ordered:
            self.stdout.write(f"  {key:38s} {metrics.get(key)}")

        self.stdout.write(self.style.MIGRATE_HEADING("Per category"))
        for cat, bucket in sorted(metrics["per_category"].items()):
            self.stdout.write(
                f"  {cat:28s} n={bucket['n']:3d} correct={bucket['correct']:3d} served={bucket['served']:3d}")

        if result["confusion"]:
            self.stdout.write(self.style.MIGRATE_HEADING("Confusion (wrong or unexpected rows)"))
            for row in result["confusion"]:
                self.stdout.write(
                    f"  {row['query'][:44]:46s} expected={row['expected_topic'][:28]:30s} "
                    f"served={(row['served_topic'] or '(none)')[:28]:30s} "
                    f"top1={row['top1_topic'][:24]:26s} {row['top1_score']:.4f} "
                    f"margin={row['margin']:.4f} [{row['outcome']}]")

        if options["csv_out"]:
            fieldnames = list(result["rows"][0].keys())
            with open(options["csv_out"], "w", newline="", encoding="utf-8") as fh:
                writer = csv_module.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(result["rows"])
            self.stdout.write(f"Per-row CSV written to {options['csv_out']}")

        if options["json_out"]:
            payload = {
                "metrics": metrics,
                "rows": result["rows"],
                "confusion": result["confusion"],
                "provenance": {
                    "git_sha": evaluation.git_sha(),
                    "model_id": settings.EMBEDDING_MODEL_ID,
                    "model_artifact": evaluation.model_artifact_fingerprint(),
                    "runtime": evaluation.runtime_descriptor(),
                    "index_fingerprint": evaluation.current_index_fingerprint(),
                    "labelled_file": path.name,
                },
            }
            Path(options["json_out"]).write_text(
                json.dumps(payload, indent=1), encoding="utf-8")
            self.stdout.write(f"Full JSON written to {options['json_out']}")
