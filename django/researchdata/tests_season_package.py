"""
Failing contract: allowlist package builder, prohibited-path inspector,
deterministic double build, and planted contamination.
"""

import importlib.util
import sys
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_SCRIPT = REPO_ROOT / "scripts" / "package_workshop.py"


def load_package_workshop():
    if not PACKAGE_SCRIPT.is_file():
        raise FileNotFoundError(PACKAGE_SCRIPT)
    spec = importlib.util.spec_from_file_location("package_workshop", PACKAGE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["package_workshop"] = mod
    spec.loader.exec_module(mod)
    return mod


class PackageWorkshopTests(SimpleTestCase):
    def test_package_script_exists(self):
        self.assertTrue(PACKAGE_SCRIPT.is_file(), "scripts/package_workshop.py is required")

    def test_inspector_rejects_contaminated_fixtures(self):
        mod = load_package_workshop()
        issues = mod.inspect_distribution(REPO_ROOT / "web_extension_chrome")
        self.assertTrue(issues, "current extension tree must fail the workshop inspector")

    def test_clean_allowlist_build_is_deterministic(self):
        mod = load_package_workshop()
        first = mod.build_zip(source_dir=REPO_ROOT / "web_extension_chrome")
        second = mod.build_zip(source_dir=REPO_ROOT / "web_extension_chrome")
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(first["filename"], "investigating-search-interface-season-2026-v0.2.1.zip")


class PackageContaminationInvertedTests(SimpleTestCase):
    def test_each_contamination_class_fails_inspection(self):
        mod = load_package_workshop()
        cases = (
            "undeclared_file",
            "symlink",
            "localhost_host",
            "wildcard_host",
            "hidden_research_route",
            "installation_id_key",
            "unexpected_network_path",
        )
        for case in cases:
            with self.subTest(case=case):
                issues = mod.inspect_fixture(case)
                self.assertTrue(issues, f"planted {case} must make the inspector fail")

    def test_exclusion_preference_is_not_treated_as_prohibited(self):
        mod = load_package_workshop()
        issues = mod.inspect_text("isi_topics_exclude")
        self.assertFalse(
            issues,
            "topic exclusions are a workshop user setting, not a prohibited research path",
        )

    def test_manifest_description_over_132_fails_the_gate(self):
        mod = load_package_workshop()
        planted = "x" * 133
        payload = {
            "name": mod.EXPECTED_NAME,
            "version": mod.EXPECTED_VERSION,
            "version_name": mod.EXPECTED_VERSION_NAME,
            "description": planted,
            "host_permissions": [mod.EXPECTED_HOST_PERMISSION],
            "permissions": ["storage"],
            "content_scripts": [{"matches": list(mod.EXPECTED_GOOGLE_MATCHES)}],
            "icons": dict(mod.EXPECTED_ICONS),
        }
        issues = mod._validate_manifest(payload)
        self.assertTrue(any("description length" in item for item in issues))
        payload["description"] = "x" * 132
        restored = mod._validate_manifest(payload)
        self.assertFalse(any("description length" in item for item in restored))

    def test_hostile_suffix_origin_fails_the_gate(self):
        mod = load_package_workshop()
        hostile = "https://investigating-search-interface.onrender.com.evil.example/collect"
        self.assertTrue(hostile.startswith(mod.EXPECTED_API))
        issues = mod.inspect_text(f"fetch('{hostile}');")
        self.assertIn("unexpected_network_path", issues)
        allowed = mod.inspect_text(f"fetch('{mod.EXPECTED_API}/data/prompt/get/');")
        self.assertNotIn("unexpected_network_path", allowed)
