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
        self.assertEqual(first["filename"], "investigating-search-interface-season-2026-v2.2.0.zip")


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
