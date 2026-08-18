#!/usr/bin/env python3
"""Verify requirements-release.lock matches the committed SHA-256 digest."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK = REPO_ROOT / "requirements-release.lock"
DIGEST = REPO_ROOT / "requirements-release.lock.sha256"


def main():
    if not LOCK.is_file():
        print(f"missing {LOCK}", file=sys.stderr)
        return 1
    if not DIGEST.is_file():
        print(f"missing {DIGEST}", file=sys.stderr)
        return 1
    expected = DIGEST.read_text(encoding="utf-8").split()[0].strip().lower()
    actual = hashlib.sha256(LOCK.read_bytes()).hexdigest()
    if actual != expected:
        print(
            f"requirements-release.lock hash mismatch: expected {expected}, got {actual}",
            file=sys.stderr,
        )
        return 1
    print(f"requirements-release.lock sha256={actual}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
