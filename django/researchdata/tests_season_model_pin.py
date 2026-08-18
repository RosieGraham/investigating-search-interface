"""
Failing contract: download_model pins a Hugging Face revision and checks hashes.
"""

import inspect

from django.test import SimpleTestCase

from researchdata.management.commands import download_model


class ModelPinTests(SimpleTestCase):
    def test_download_model_passes_revision(self):
        src = inspect.getsource(download_model.Command.handle)
        self.assertIn("revision", src)
        self.assertIn("hf_hub_download", src)
        self.assertRegex(src, r"hf_hub_download\([^)]*revision=")

    def test_verify_model_artifacts_helper_exists(self):
        try:
            from researchdata.model_pin import verify_model_artifacts
        except ImportError as exc:
            self.fail(f"verify_model_artifacts is required: {exc}")
        with self.assertRaises(Exception):
            verify_model_artifacts(
                model_dir="/tmp/isi-missing-model-dir",
                expected_model_sha256="0" * 64,
                expected_tokenizer_sha256="1" * 64,
                expected_onnx_filename="model_qint8_avx512_vnni.onnx",
            )
