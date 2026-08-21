"""
Failing contract: committed workshop release-policy, fail-closed identity cases.
"""

import json
from pathlib import Path

from django.test import SimpleTestCase, TestCase

from .tests_season_support import BUILD_ID, EXT_VERSION, NOTICE_VERSION, POLICY_RELATIVE

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = REPO_ROOT / POLICY_RELATIVE


class ReleasePolicyFileTests(SimpleTestCase):
    def test_policy_file_exists(self):
        self.assertTrue(POLICY_PATH.is_file(), f"missing {POLICY_RELATIVE}")

    def test_policy_binds_release_identity(self):
        data = json.loads(POLICY_PATH.read_text())
        self.assertEqual(data["build_id"], BUILD_ID)
        self.assertEqual(data["mode"], "workshop")
        self.assertEqual(data["extension_version"], EXT_VERSION)
        self.assertEqual(data["notice_version"], NOTICE_VERSION)
        self.assertEqual(
            data["notice_url"],
            "https://investigating-search-interface.onrender.com/privacy/",
        )
        self.assertEqual(
            data["project_url"],
            "https://investigating-search-interface.onrender.com/",
        )
        self.assertEqual(data["active_window"]["start"], "2026-08-20T00:00:00Z")
        self.assertEqual(data["active_window"]["end"], "2026-09-18T23:59:59Z")
        self.assertEqual(data["capabilities"], ["matching"])


class ReleasePolicyValidationTests(TestCase):
    def test_load_and_validate_helpers_exist(self):
        try:
            from researchdata.release_policy import load_workshop_policy
        except ImportError as exc:
            self.fail(f"release_policy helpers are required: {exc}")
        policy = load_workshop_policy()
        self.assertEqual(policy["build_id"], BUILD_ID)

    def test_identity_cases_fail_closed(self):
        from researchdata.release_policy import IdentityError, validate_request_identity

        cases = [
            {"build_id": None, "version": EXT_VERSION},
            {"build_id": "", "version": EXT_VERSION},
            {"build_id": "unknown-build", "version": EXT_VERSION},
            {"build_id": BUILD_ID, "version": "2.1.0"},
            {"build_id": BUILD_ID, "version": None},
            {"build_id": "season-2026-expired", "version": EXT_VERSION},
            {"build_id": "test-only-non-workshop", "version": EXT_VERSION},
        ]
        for case in cases:
            with self.subTest(**case):
                with self.assertRaises(IdentityError):
                    validate_request_identity(
                        build_id=case["build_id"],
                        extension_version=case["version"],
                    )

    def test_valid_workshop_identity_is_accepted(self):
        from researchdata.release_policy import validate_request_identity

        validate_request_identity(build_id=BUILD_ID, extension_version=EXT_VERSION)
