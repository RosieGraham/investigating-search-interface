"""
Spot-check the LIVE classifier from anywhere, no Django needed.

    python scripts/spot_check_live.py "flat earth proof" "covid vaccine"
    python scripts/spot_check_live.py --file queries.txt --csv out.csv

Hits the read-only /data/classifier/debug/ endpoint (added 2026-06-12; must be
deployed first) and prints the top matches with confidences, flagging which
clear the live threshold. With --csv it writes one row per query in the same
column layout as the intern's query log sheet, so intern strand A and the
per-batch regression checks share a format.

Falls back to /data/prompt/get/ when the debug endpoint is not deployed yet,
in which case only topics with approved prompts are visible (and that
limitation is printed loudly).
"""

import argparse
import csv
import json
import sys
import time
import urllib.parse
import urllib.request

BASE = "https://investigating-search-interface.onrender.com"
DEBUG_PATH = "/data/classifier/debug/"
FALLBACK_PATH = "/data/prompt/get/"


def fetch(path, query):
    url = BASE + path + "?" + urllib.parse.urlencode({"user_search_query": query})
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.load(resp)


def check_query(query, use_fallback):
    if not use_fallback:
        data = fetch(DEBUG_PATH, query)
        return [
            (m["topic"], m["group"], m["confidence"], m["above_threshold"])
            for m in data.get("matches", [])
        ], data.get("threshold")
    data = fetch(FALLBACK_PATH, query)
    seen = []
    for prompt in data.get("prompts", []):
        seen.append((prompt.get("topic"), None, prompt.get("confidence"), True))
    return seen, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("queries", nargs="*", help="queries to test")
    ap.add_argument("--file", help="text file with one query per line")
    ap.add_argument("--csv", help="write results to this CSV file")
    ap.add_argument("--sleep", type=float, default=0.5,
                    help="seconds between requests (be kind to the free tier)")
    args = ap.parse_args()

    queries = list(args.queries)
    if args.file:
        with open(args.file) as f:
            queries += [line.strip() for line in f if line.strip()]
    if not queries:
        ap.error("no queries given")

    # Probe for the debug endpoint once.
    use_fallback = False
    try:
        fetch(DEBUG_PATH, "test")
    except Exception:
        use_fallback = True
        print("NOTE: debug endpoint not reachable; falling back to prompt/get.")
        print("Only topics with APPROVED prompts are visible this way.\n")

    rows = []
    for query in queries:
        try:
            matches, threshold = check_query(query, use_fallback)
        except Exception as exc:
            print(f"  ERROR for {query!r}: {exc}")
            rows.append([query, "ERROR", "", "", ""])
            continue
        top = matches[0] if matches else ("NO MATCH", "", "", False)
        marker = "ok " if (matches and top[3]) else "?? "
        conf = f"{top[2]:.3f}" if matches and top[2] is not None else ""
        print(f"{marker}{query!r} -> {top[0]} ({conf})"
              + (f"  [threshold {threshold}]" if threshold is not None else ""))
        for extra in matches[1:3]:
            extra_conf = f"{extra[2]:.3f}" if extra[2] is not None else ""
            print(f"      next: {extra[0]} ({extra_conf})")
        rows.append([
            query,
            top[0] if matches else "NO MATCH",
            conf,
            "yes" if (matches and top[3]) else "no",
            "; ".join(f"{m[0]} {m[2]:.3f}" for m in matches[1:3] if m[2] is not None),
        ])
        time.sleep(args.sleep)

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["query", "top_match", "confidence",
                             "above_threshold", "runners_up"])
            writer.writerows(rows)
        print(f"\nWrote {len(rows)} rows to {args.csv}")


if __name__ == "__main__":
    sys.exit(main())
