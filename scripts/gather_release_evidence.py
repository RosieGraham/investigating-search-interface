#!/usr/bin/env python3
"""Gather SEASON release provenance fields that can be observed.

Does not fabricate later-phase fields. Missing values are recorded as
status=not_run or pending, never guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "release-evidence" / "season-2026" / "schema.json"
DEFAULT_OUT = REPO_ROOT / "release-evidence" / "season-2026" / "gathered.json"

PENDING_STORE_ITEM_ID = {
    "value": None,
    "status": "pending",
    "owner": "Rosie",
    "note": (
        "Store item ID is required at Phase 5, not Gate 4. Leave empty until "
        "the reviewed ZIP is uploaded to create the item."
    ),
}


def git(argv, cwd=REPO_ROOT):
    proc = subprocess.run(
        ["git", *argv],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def require_git(argv):
    code, out, err = git(argv)
    if code != 0:
        raise RuntimeError(f"git {' '.join(argv)} failed ({code}): {err or out}")
    return out


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def not_run(reason):
    return {"status": "not_run", "value": None, "reason": reason}


def load_gates(path):
    if path is None:
        return None
    return json.loads(Path(path).read_text())


def gather(gates=None, extra_notes=None):
    head = require_git(["rev-parse", "HEAD"])
    branch = require_git(["rev-parse", "--abbrev-ref", "HEAD"])
    porcelain = require_git(["status", "--porcelain=v1"])
    origin_main_code, origin_main, origin_err = git(["rev-parse", "origin/main"])
    log_subject = require_git(["log", "-1", "--format=%H%n%s%n%cI"])
    log_lines = log_subject.splitlines()
    tracked_code, tracked, _ = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])

    source = {
        "repository": "https://github.com/RosieGraham/investigating-search-interface",
        "workspace_root": str(REPO_ROOT),
        "branch": branch,
        "head": head,
        "origin_main": origin_main if origin_main_code == 0 else None,
        "origin_main_error": origin_err or None,
        "head_equals_origin_main": bool(origin_main) and head == origin_main,
        "working_tree_clean": porcelain == "",
        "porcelain": porcelain.splitlines() if porcelain else [],
        "upstream": tracked if tracked_code == 0 else None,
        "head_subject": log_lines[1] if len(log_lines) > 1 else None,
        "head_committer_date": log_lines[2] if len(log_lines) > 2 else None,
        "nested_clone_is_workspace": (
            REPO_ROOT.name == "investigating-search-interface"
            and REPO_ROOT.parent.name == "investigating-search-interface"
        ),
    }

    schema_hash = sha256_file(SCHEMA_PATH) if SCHEMA_PATH.is_file() else None
    packet = {
        "schema": str(SCHEMA_PATH.relative_to(REPO_ROOT)),
        "schema_sha256": schema_hash,
        "gathered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gatherer": "scripts/gather_release_evidence.py",
        "python_executable": sys.executable,
        "python_version": sys.version,
        "source": source,
        "store_item_id": PENDING_STORE_ITEM_ID,
        "changed_files": not_run("No reviewed target commit yet; list from git diff after implementation."),
        "model_session_provenance": {
            "implementer": "Cursor Grok 4.6 High",
            "role": "Accountable implementer",
            "reviewers": not_run("Independent review is after Gate 4."),
        },
        "data_lifecycle_matrix": not_run("Phase 3/4 network capture."),
        "commands_and_results": gates or not_run("Pass --gates <run_release_gates.json>."),
        "contract_tests_red_before_green": not_run("Phase 1."),
        "readiness_failure_injection": not_run("Phase 2."),
        "inverted_controls": not_run("Phase 1 planted-fault tests."),
        "frozen_result_table": not_run("Phase 4; six contaminated queries remain excluded."),
        "load_test": not_run("Phase 4 isolated, Phase 5 live with Rosie approval."),
        "release_lock": not_run("Phase 2 hashed lock."),
        "package": not_run("Phase 4 builder."),
        "runtime_browser": not_run("Phase 4."),
        "matching_replay": not_run("Phase 4."),
        "synthetic_marker_audit": not_run("Phase 4."),
        "research_use_transition": not_run("Rosie-owned Phase 5 record."),
        "independent_review": not_run("Fresh-session review after Gate 4."),
        "frozen_release_manifest": not_run("Gate 4 freeze."),
        "known_limitations": extra_notes or [],
    }
    return packet


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gates", help="JSON from scripts/run_release_gates.py --json-out")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--note", action="append", default=[], help="Known-limitation note")
    args = parser.parse_args(argv)

    packet = gather(gates=load_gates(args.gates), extra_notes=args.note)
    out = Path(args.out)
    if not out.is_absolute():
        out = REPO_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(packet, indent=2) + "\n")
    print(f"wrote {out}")
    print(f"head={packet['source']['head']}")
    print(f"branch={packet['source']['branch']}")
    print(f"working_tree_clean={packet['source']['working_tree_clean']}")
    if isinstance(packet["commands_and_results"], dict):
        print(f"discovered_test_count={packet['commands_and_results'].get('discovered_test_count')}")
    else:
        print("discovered_test_count=NOT_RUN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
