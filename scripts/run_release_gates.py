#!/usr/bin/env python3
"""Run the SEASON release gates and print the discovered Django test count.

This is the shared local/CI runner. It does not hide production-check warnings,
does not download the ONNX model, and does not invent a passing result.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DJANGO_DIR = REPO_ROOT / "django"
EXTENSION_DIR = REPO_ROOT / "web_extension_chrome"
TEST_COUNT_RE = re.compile(r"^Ran (\d+) tests? in ", re.MULTILINE)

# Long dummy key so security.W009 does not mask the real deploy warning (W004).
# This is not a production secret.
DEPLOY_CHECK_SECRET_KEY = (
    "ci-deploy-check-not-a-production-secret-xxxxxxxxxxxxxxxxxxxx"
)
DEPLOY_CHECK_HOST = "investigating-search-interface.onrender.com"


def run_cmd(name, argv, cwd, env=None, stdin=None):
    merged = os.environ.copy()
    if env:
        merged.update(env)
    proc = subprocess.run(
        argv,
        cwd=cwd,
        env=merged,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return {
        "name": name,
        "argv": argv,
        "cwd": str(cwd),
        "exit_code": proc.returncode,
        "output": output,
    }


def parse_test_count(output):
    match = TEST_COUNT_RE.search(output or "")
    if not match:
        return None
    return int(match.group(1))


def django_env(debug=True, extra=None):
    env = {
        "DEBUG": "true" if debug else "false",
        "DATABASE_URL": os.environ.get("DATABASE_URL", "sqlite:////tmp/isi-gates.sqlite3"),
        "DJANGO_SETTINGS_MODULE": "core.settings",
    }
    if extra:
        env.update(extra)
    return env


def js_files():
    return sorted(p for p in EXTENSION_DIR.glob("*.js") if p.is_file() and not p.is_symlink())


def run_gates():
    python = sys.executable
    results = []

    results.append(run_cmd(
        "flake8",
        [python, "-m", "flake8", "."],
        REPO_ROOT,
    ))

    test_result = run_cmd(
        "django_tests",
        [python, "manage.py", "test", "researchdata", "-v", "2"],
        DJANGO_DIR,
        env=django_env(debug=True),
    )
    test_result["discovered_test_count"] = parse_test_count(test_result["output"])
    results.append(test_result)

    results.append(run_cmd(
        "migration_check",
        [python, "manage.py", "makemigrations", "--check", "--dry-run", "-v", "1"],
        DJANGO_DIR,
        env=django_env(debug=True),
    ))

    results.append(run_cmd(
        "check_deploy",
        [python, "manage.py", "check", "--deploy", "-v", "2"],
        DJANGO_DIR,
        env=django_env(debug=False, extra={
            "SECRET_KEY": DEPLOY_CHECK_SECRET_KEY,
            "ALLOWED_HOSTS": DEPLOY_CHECK_HOST,
        }),
    ))

    js_output_parts = []
    js_exit = 0
    for path in js_files():
        item = run_cmd(
            f"js_syntax:{path.name}",
            ["node", "--check", str(path)],
            REPO_ROOT,
        )
        js_output_parts.append(f"$ node --check {path}\n{item['output']}")
        if item["exit_code"] != 0:
            js_exit = item["exit_code"]
    results.append({
        "name": "js_syntax",
        "argv": ["node", "--check", "<web_extension_chrome/*.js>"],
        "cwd": str(REPO_ROOT),
        "exit_code": js_exit,
        "output": "\n".join(js_output_parts),
        "files": [str(p.relative_to(REPO_ROOT)) for p in js_files()],
    })

    results.append(run_cmd(
        "js_contract",
        ["node", "--test", "web_extension_chrome/test/request_lifecycle.test.mjs",
         "web_extension_chrome/test/acknowledgement.test.mjs",
         "web_extension_chrome/test/popup_contract.test.mjs",
         "web_extension_chrome/test/manifest_matches.test.mjs"],
        REPO_ROOT,
    ))

    manifest_path = EXTENSION_DIR / "manifest.json"
    results.append(run_cmd(
        "manifest_parse",
        [python, "-c", (
            "import json, pathlib, sys; "
            "p = pathlib.Path(sys.argv[1]); "
            "data = json.loads(p.read_text()); "
            "print('parsed_ok=true'); "
            "print('name=', data.get('name')); "
            "print('version=', data.get('version'))"
        ), str(manifest_path)],
        REPO_ROOT,
    ))

    results.append(run_cmd(
        "package_workshop",
        [
            python,
            str(REPO_ROOT / "scripts" / "package_workshop.py"),
            "--verify-double-build",
            "--out-dir",
            "/tmp/isi-season-package",
        ],
        REPO_ROOT,
    ))

    return results


def summarise(results):
    test = next((r for r in results if r["name"] == "django_tests"), None)
    deploy = next((r for r in results if r["name"] == "check_deploy"), None)
    failed = [r["name"] for r in results if r["exit_code"] != 0]
    return {
        "python_executable": sys.executable,
        "python_version": sys.version,
        "discovered_test_count": None if test is None else test.get("discovered_test_count"),
        "failed_gates": failed,
        "all_exit_zero": not failed,
        "check_deploy_output": None if deploy is None else deploy["output"],
        "results": [
            {
                "name": r["name"],
                "argv": r["argv"],
                "cwd": r["cwd"],
                "exit_code": r["exit_code"],
                "discovered_test_count": r.get("discovered_test_count"),
                "output": r["output"],
            }
            for r in results
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", help="Write the full gate record as JSON")
    args = parser.parse_args(argv)

    summary = summarise(run_gates())
    print("=== SEASON release gates ===")
    print(f"python: {summary['python_version'].splitlines()[0]}")
    print(f"discovered_test_count: {summary['discovered_test_count']}")
    for result in summary["results"]:
        print(f"{result['name']}: exit={result['exit_code']}")
    print("=== check --deploy output (warnings must remain visible) ===")
    print(summary["check_deploy_output"] or "")
    if summary["failed_gates"]:
        print(f"FAILED gates: {', '.join(summary['failed_gates'])}")
    else:
        print("all gates exit 0")

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=2) + "\n")
        print(f"wrote {out}")

    return 0 if summary["all_exit_zero"] and summary["discovered_test_count"] is not None else 1


if __name__ == "__main__":
    sys.exit(main())
