"""
Run the Phase 2 matching diagnostics and print (or export) the results.

    python django/manage.py matching_diagnostics
    python django/manage.py matching_diagnostics --json out.json --top-pairs 30
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from researchdata import diagnostics


class Command(BaseCommand):
    help = "Attractor pairs, self-retrieval and out-of-domain diagnostics for the topic index."

    def add_arguments(self, parser):
        parser.add_argument("--top-pairs", type=int, default=25)
        parser.add_argument("--json", dest="json_out", default=None)
        parser.add_argument(
            "--ood", default="weather tomorrow,pizza near me,gmail login,bbc news,train times to london",
            help="Comma-separated out-of-domain queries.")

    def handle(self, *args, **options):
        pairs = diagnostics.attractor_pairs(top_n=options["top_pairs"])
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Attractor pairs (top {options['top_pairs']} by representation similarity)"))
        for p in pairs:
            flags = f"[{'D' if p['a_described'] else '-'}{'D' if p['b_described'] else '-'}]"
            self.stdout.write(
                f"  {p['similarity']:.4f} {flags} {p['topic_a']}  <->  {p['topic_b']}")

        sr = diagnostics.self_retrieval()
        failing = [s for s in sr["summary"] if not s["passes"]]
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Self-retrieval ({len(sr['summary'])} described topics, "
            f"{len(failing)} failing)"))
        for s in failing:
            self.stdout.write(f"  FAIL {s['topic']}: {s['failures']}/{s['queries']} seed queries lost")
        for row in sr["rows"]:
            if row["rank"] != 1:
                self.stdout.write(
                    f"    '{row['query']}' (owner {row['topic']}) -> rank {row['rank']}, "
                    f"captured by {row['top1']}, margin {row['margin']:+.4f}")

        ood_queries = [q.strip() for q in options["ood"].split(",") if q.strip()]
        ood = diagnostics.out_of_domain_scores(ood_queries)
        self.stdout.write(self.style.MIGRATE_HEADING("Out-of-domain max similarities"))
        for row in ood:
            self.stdout.write(
                f"  {row['max_similarity']:.4f}  {row['query']}  (nearest: {row['nearest_topic']})")

        if options["json_out"]:
            payload = {"attractor_pairs": pairs, "self_retrieval": sr, "out_of_domain": ood}
            Path(options["json_out"]).write_text(json.dumps(payload, indent=1), encoding="utf-8")
            self.stdout.write(f"JSON written to {options['json_out']}")
