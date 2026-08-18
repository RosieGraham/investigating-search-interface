"""
Query-free release readiness. Fail closed. Do not run classifier canaries here.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings

from .embedding import classifier_status
from .model_pin import ModelPinError, verify_model_artifacts
from .release_policy import load_workshop_policy, policy_sha256


EXPECTED_THRESHOLD = 0.35
EXPECTED_MARGIN = 0.00


def _config_from_settings():
    from .classifier_config import (
        get_classifier_margin,
        get_classifier_threshold,
        get_serve_placeholders,
    )

    return {
        "threshold": get_classifier_threshold(),
        "margin": get_classifier_margin(),
        "trigger_fallback": bool(settings.TRIGGER_FALLBACK_ENABLED),
        "placeholders": bool(get_serve_placeholders()),
        "research_writes_enabled": bool(settings.RESEARCH_WRITES_ENABLED),
        "settings_threshold": float(settings.CLASSIFIER_THRESHOLD),
    }


def evaluate_readiness(
    model_path=None,
    effective_config=None,
    model_ok=None,
    ignore_model=False,
):
    failures = []

    if ignore_model:
        failures.append("ignore_model is not an allowed readiness bypass")
    if model_ok is False:
        failures.append("model check reported not ok")

    try:
        policy = load_workshop_policy()
    except Exception as exc:
        return {
            "ready": False,
            "failures": [f"policy: {exc}"],
            "policy_sha256": None,
            "git_commit": os.environ.get("RENDER_GIT_COMMIT", "unknown")[:12],
        }

    expected = policy["config"]
    config = effective_config if effective_config is not None else _config_from_settings()

    if abs(float(config.get("threshold", 0)) - EXPECTED_THRESHOLD) > 1e-9:
        failures.append("threshold is not 0.35")
    if abs(float(config.get("margin", 0)) - EXPECTED_MARGIN) > 1e-9:
        failures.append("margin is not 0.00")
    if config.get("trigger_fallback"):
        failures.append("trigger fallback must be false")
    if config.get("placeholders"):
        failures.append("placeholders must be false")
    if config.get("research_writes_enabled"):
        failures.append("research writes must be disabled")

    if effective_config is None:
        settings_threshold = float(config.get("settings_threshold", settings.CLASSIFIER_THRESHOLD))
        if abs(float(config["threshold"]) - settings_threshold) > 1e-9:
            failures.append("cached database threshold disagrees with settings")
        if abs(settings_threshold - float(expected["threshold"])) > 1e-9:
            failures.append("settings threshold disagrees with release policy")

    if model_path:
        if not Path(model_path).is_file():
            failures.append("model file missing")
    elif model_ok is not False:
        try:
            verify_model_artifacts(
                model_dir=str(Path(settings.EMBEDDING_MODEL_DIR)),
                expected_model_sha256=policy["model"]["model_sha256"],
                expected_tokenizer_sha256=policy["model"]["tokenizer_sha256"],
                expected_onnx_filename=policy["model"]["onnx_filename"],
            )
        except ModelPinError as exc:
            failures.append(str(exc))

    if effective_config is None:
        try:
            status = classifier_status()
            if not status.get("model_present"):
                failures.append("model not present")
            if int(status.get("index_topics") or 0) < 1:
                failures.append("topic index empty")
            if status.get("index_dirty"):
                failures.append("topic index dirty")
        except Exception as exc:
            failures.append(f"classifier status: {exc}")

    ready = not failures
    return {
        "ready": ready,
        "failures": failures,
        "policy_sha256": policy_sha256(),
        "git_commit": os.environ.get("RENDER_GIT_COMMIT", "unknown")[:12],
        "build_id": policy["build_id"],
    }
