"""Pin and verify the workshop ONNX encoder and tokenizer."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .embedding import MODEL_FILENAME, TOKENIZER_FILENAME


class ModelPinError(Exception):
    """Model directory, filename, or hash does not match the pinned identity."""


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model_artifacts(
    model_dir,
    expected_model_sha256,
    expected_tokenizer_sha256,
    expected_onnx_filename,
):
    directory = Path(model_dir)
    if not directory.is_dir():
        raise ModelPinError(f"model directory missing: {directory}")
    if not expected_onnx_filename:
        raise ModelPinError("expected ONNX filename is required")

    model_path = directory / MODEL_FILENAME
    tokenizer_path = directory / TOKENIZER_FILENAME
    if not model_path.is_file():
        raise ModelPinError(f"missing ONNX file {model_path} (source {expected_onnx_filename})")
    if not tokenizer_path.is_file():
        raise ModelPinError(f"missing tokenizer {tokenizer_path}")

    model_hash = _sha256_file(model_path)
    tokenizer_hash = _sha256_file(tokenizer_path)
    if model_hash != expected_model_sha256:
        raise ModelPinError("ONNX hash mismatch")
    if tokenizer_hash != expected_tokenizer_sha256:
        raise ModelPinError("tokenizer hash mismatch")
    return {
        "model_path": str(model_path),
        "tokenizer_path": str(tokenizer_path),
        "model_sha256": model_hash,
        "tokenizer_sha256": tokenizer_hash,
        "onnx_filename": expected_onnx_filename,
    }
