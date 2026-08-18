"""
Committed workshop release-policy loader and fail-closed identity checks.

The policy is a file, not a database row. Do not persist queries, participant
IDs, request history, or per-request build records here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from django.conf import settings

REQUIRED_KEYS = (
    "build_id",
    "mode",
    "extension_version",
    "notice_version",
    "notice_url",
    "project_url",
    "active_window",
    "capabilities",
    "config",
    "model",
)


class IdentityError(Exception):
    """Missing, unknown, inactive, expired, or version-mismatched release identity."""


class PolicyError(Exception):
    """The committed policy file is missing or malformed."""


def policy_path():
    override = getattr(settings, "RELEASE_POLICY_PATH", None)
    if override:
        return Path(override)
    return Path(settings.BASE_DIR).parent / "release-policy" / "season-2026-workshop.json"


def current_time():
    override = getattr(settings, "RELEASE_CLOCK_OVERRIDE", None)
    if override:
        if isinstance(override, datetime):
            if override.tzinfo is None:
                return override.replace(tzinfo=timezone.utc)
            return override
        text = str(override).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return datetime.now(timezone.utc)


def _parse_window_instant(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _validate_policy_dict(data):
    if not isinstance(data, dict):
        raise PolicyError("release policy must be a JSON object")
    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        raise PolicyError(f"release policy missing keys: {missing}")
    if data["mode"] != "workshop":
        raise PolicyError("release policy mode must be workshop")
    if data.get("capabilities") != ["matching"]:
        raise PolicyError("workshop policy capabilities must be matching only")
    window = data["active_window"]
    if not isinstance(window, dict) or "start" not in window or "end" not in window:
        raise PolicyError("active_window must include start and end")
    config = data["config"]
    for key in ("threshold", "margin", "trigger_fallback", "placeholders", "research_writes_enabled"):
        if key not in config:
            raise PolicyError(f"release policy config missing {key}")
    model = data["model"]
    for key in ("id", "revision", "onnx_filename", "model_sha256", "tokenizer_sha256"):
        if key not in model:
            raise PolicyError(f"release policy model missing {key}")
    return data


@lru_cache(maxsize=1)
def _load_policy_cached(path_str, digest):
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return _validate_policy_dict(data)


def load_workshop_policy():
    path = policy_path()
    if not path.is_file():
        raise PolicyError(f"missing release policy: {path}")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    return _load_policy_cached(str(path.resolve()), digest)


def policy_sha256():
    path = policy_path()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_request_identity(build_id, extension_version, now=None):
    if not build_id or not extension_version:
        raise IdentityError("missing identity")
    try:
        policy = load_workshop_policy()
    except PolicyError as exc:
        raise IdentityError("policy unavailable") from exc
    if build_id != policy["build_id"]:
        raise IdentityError("unknown or expired build")
    if extension_version != policy["extension_version"]:
        raise IdentityError("stale extension version")
    moment = now if now is not None else current_time()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    start = _parse_window_instant(policy["active_window"]["start"])
    end = _parse_window_instant(policy["active_window"]["end"])
    if moment < start or moment > end:
        raise IdentityError("inactive window")


def workshop_writes_allowed(build_id):
    """Workshop builds never write, even if RESEARCH_WRITES_ENABLED is later true."""
    try:
        policy = load_workshop_policy()
    except PolicyError:
        return False
    if build_id == policy["build_id"]:
        return False
    return True
