"""
Failing contract: workshop client has no research paths, versioned notice,
bounded retry, and no empty popup lookup. Topic exclusions stay.
"""

import json
import re
from pathlib import Path

from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]
EXT = REPO_ROOT / "web_extension_chrome"


def _read(name):
    return (EXT / name).read_text()


class WorkshopClientScanTests(SimpleTestCase):
    def test_background_has_no_research_handlers(self):
        src = _read("background.js")
        for needle in ("postReport", "postResponse", "logEvent", "getInstallationId", "isi_installation_id"):
            self.assertNotIn(needle, src, f"workshop background still contains {needle}")

    def test_content_has_no_research_ui_or_events(self):
        src = _read("content.js")
        for needle in ("postReport", "postResponse", "logEvent", "isi_consent_given", "isi_log_events"):
            self.assertNotIn(needle, src, f"workshop content still contains {needle}")

    def test_popup_does_not_health_check_or_empty_lookup(self):
        src = _read("popup.js")
        self.assertNotIn("type: 'health'", src)
        self.assertNotIn('type: "health"', src)
        self.assertNotIn("query: ''", src)
        self.assertNotIn('query: ""', src)

    def test_popup_keeps_topic_exclusions(self):
        src = _read("popup.js")
        self.assertIn("isi_topics_exclude", src)

    def test_content_keeps_topic_exclusions_as_local_preference(self):
        src = _read("content.js")
        self.assertIn("topicsExclude", src)
        self.assertIn("isi_topics_exclude", src)

    def test_notice_is_versioned_season_key(self):
        src = _read("content.js") + _read("popup.js") + _read("config.js")
        self.assertIn("season-2026-v2", src)
        self.assertNotIn("isi_consent_given", src)

    def test_consent_and_footer_match_signed_strings(self):
        src = _read("content.js")
        self.assertIn(
            "This workshop release sends the text of your search to the project's server so it can select one reflection prompt.",
            src,
        )
        self.assertIn("If you have excluded any topics, that choice is sent too.", src)
        self.assertNotIn("University of Birmingham server", src)
        self.assertIn(
            "Alpha build 0.2.1 · prompts appear alongside your results and never change them",
            src,
        )
        popup = _read("popup.html")
        self.assertIn("SEASON 2026 workshop", popup)
        self.assertNotIn("University of Birmingham", popup)

    def test_config_has_workshop_privacy_and_project_urls(self):
        src = _read("config.js")
        self.assertIn("https://investigating-search-interface.onrender.com/privacy/", src)
        self.assertIn("https://investigating-search-interface.onrender.com/", src)
        self.assertNotIn("github.com", src)
        self.assertNotIn("bear-rsg/ethical-interface", src)

    def test_manifest_is_season_identity_with_exact_host(self):
        src = _read("manifest.json")
        self.assertIn('"name": "Investigating Search Interface"', src)
        self.assertIn('"version": "0.2.1"', src)
        self.assertIn('"version_name": "Alpha build 0.2.1"', src)
        self.assertNotIn("localhost", src)
        self.assertNotIn("*.onrender.com", src)
        self.assertIn("https://investigating-search-interface.onrender.com/*", src)

    def test_same_page_retry_state_machine_is_present(self):
        src = _read("content.js")
        self.assertIn("350", src)
        self.assertTrue(
            re.search(r"FIRST_ATTEMPT_TIMEOUT_MS = 12000", src),
            "first attempt must use a twelve-second timeout",
        )

    def test_observer_timeout_outlives_two_attempt_request_path(self):
        config = _read("config.js")
        match = re.search(r"OBSERVER_TIMEOUT:\s*(\d+)", config)
        self.assertIsNotNone(match, "config.js must set OBSERVER_TIMEOUT")
        observer_ms = int(match.group(1))
        two_attempts = 12000 + 350 + 12000
        self.assertGreaterEqual(
            observer_ms,
            two_attempts,
            "layout observer must outlive 12s + 350ms retry + 12s",
        )

    def test_manifest_google_matches_include_european_hosts(self):
        data = json.loads(_read("manifest.json"))
        matches = data["content_scripts"][0]["matches"]
        required = [
            "https://www.google.com/search*",
            "https://www.google.co.uk/search*",
            "https://www.google.ie/search*",
            "https://www.google.de/search*",
            "https://www.google.fr/search*",
            "https://www.google.nl/search*",
            "https://www.google.dk/search*",
            "https://www.google.se/search*",
            "https://www.google.no/search*",
            "https://www.google.fi/search*",
            "https://www.google.es/search*",
            "https://www.google.it/search*",
            "https://www.google.be/search*",
            "https://www.google.at/search*",
            "https://www.google.ch/search*",
            "https://www.google.pl/search*",
            "https://www.google.pt/search*",
            "https://www.google.com.au/search*",
            "https://www.google.ca/search*",
        ]
        for pattern in required:
            self.assertIn(pattern, matches)
        self.assertFalse(
            any("google.*" in item or "google.*/" in item for item in matches),
            "wildcard TLD patterns are not permitted",
        )

    def test_last_key_is_not_committed_before_valid_response(self):
        src = _read("content.js")
        commit = src.find("STATE.lastKey = requestKey")
        self.assertNotEqual(commit, -1, "request lifecycle must commit lastKey")
        success_check = src.find("resp.ok")
        self.assertNotEqual(success_check, -1)
        self.assertLess(
            success_check,
            commit,
            "lastKey must be committed only after a valid response check",
        )


class ClientScanInvertedControlTests(SimpleTestCase):
    def test_prohibited_handler_scan_fails_on_baseline_background(self):
        planted = "async function logEvent() {}\nasync function postReport() {}\n"
        with self.assertRaises(AssertionError):
            self.assertNotIn("logEvent", planted)
            self.assertNotIn("postReport", planted)

    def test_empty_popup_lookup_scan_fails_on_baseline_popup(self):
        planted = "send({ type: 'health' });\nsend({ type: 'getPrompts', query: '' });\n"
        with self.assertRaises(AssertionError):
            self.assertNotIn("query: ''", planted)
            self.assertNotIn("type: 'health'", planted)
