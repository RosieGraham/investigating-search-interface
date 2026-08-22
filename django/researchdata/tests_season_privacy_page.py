"""
Failing contract: public workshop pages are anonymous, query-free, and have
no third-party scripts or fonts. Inverted controls plant the positive case
for each negative assurance.
"""

import re
from pathlib import Path

from django.db import connection
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from researchdata.models import Setting

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "django" / "general" / "templates" / "general"
PRIVACY_TEMPLATE = TEMPLATE_DIR / "privacy.html"
HOME_TEMPLATE = TEMPLATE_DIR / "home.html"
COOKIES_TEMPLATE = TEMPLATE_DIR / "cookies.html"
BASE_TEMPLATE = REPO_ROOT / "django" / "core" / "templates" / "base.html"

THIRD_PARTY_ATTR = re.compile(
    r"""(?:src|href)\s*=\s*["'](https?:)?//[^"']+["']""",
    re.IGNORECASE,
)
THIRD_PARTY_CSS = re.compile(
    r"""@import\s+(?:url\()?["']?(https?:)?//""",
    re.IGNORECASE,
)
LOCAL_HOSTS = (
    "investigating-search-interface.onrender.com",
    "127.0.0.1",
    "localhost",
)

PUBLIC_PAGES = (
    ("/", HOME_TEMPLATE),
    ("/privacy/", PRIVACY_TEMPLATE),
    ("/cookies/", COOKIES_TEMPLATE),
)


def third_party_hits(html):
    """Return remote script/font/stylesheet URLs that are not this service."""
    hits = []
    for match in THIRD_PARTY_ATTR.finditer(html):
        url = match.group(0)
        lowered = url.lower()
        if any(host in lowered for host in LOCAL_HOSTS):
            continue
        hits.append(url)
    for match in THIRD_PARTY_CSS.finditer(html):
        hits.append(match.group(0))
    return hits


class WorkshopPublicPageTests(SimpleTestCase):
    def test_anonymous_get_returns_200_for_each_public_page(self):
        for path, _template in PUBLIC_PAGES:
            with self.subTest(path=path):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 200)
                self.assertNotIn(b"login", resp.content.lower())
                self.assertEqual(list(resp.cookies.keys()), [])

    def test_head_returns_200_for_each_public_page(self):
        for path, _template in PUBLIC_PAGES:
            with self.subTest(path=path):
                resp = self.client.head(path)
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(list(resp.cookies.keys()), [])

    def test_pages_have_no_third_party_scripts_or_fonts(self):
        for path, template in PUBLIC_PAGES:
            with self.subTest(path=path):
                html = template.read_text(encoding="utf-8")
                self.assertEqual(third_party_hits(html), [])
                resp = self.client.get(path)
                rendered = resp.content.decode("utf-8")
                self.assertEqual(third_party_hits(rendered), [])
                self.assertNotIn("<script", rendered.lower())

    def test_pages_do_not_use_settings_or_base_chrome(self):
        for path, template in PUBLIC_PAGES:
            with self.subTest(path=path):
                html = template.read_text(encoding="utf-8")
                self.assertNotIn("settings_value", html)
                self.assertNotIn("extends", html)
                self.assertNotIn("fonts.googleapis.com", html)
                self.assertNotIn("cookiesmsg.js", html)


class WorkshopPrivacyCopyTests(SimpleTestCase):
    def test_privacy_serves_signed_workshop_notice(self):
        resp = self.client.get("/privacy/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"SEASON 2026 workshop release", resp.content)
        self.assertIn(b"research project of Dr Rosie Graham", resp.content)
        self.assertIn(b"R.Graham@bham.ac.uk", resp.content)
        self.assertNotIn(b"[contact address]", resp.content)
        self.assertNotIn(b"[deploy date]", resp.content)
        self.assertRegex(
            resp.content.decode("utf-8"),
            r"Version \d+\.\d+, \d{1,2} [A-Za-z]+ 20\d{2}",
        )
        self.assertIn(b"Version 1.1, 22 August 2026", resp.content)
        self.assertIn(b"Chrome Web Store User Data Policy", resp.content)
        self.assertIn(b"Limited Use", resp.content)
        self.assertIn(b"hosted by Render", resp.content)
        self.assertNotIn(b"UNSIGNED DRAFT", resp.content)

    def test_home_is_approved_landing_copy(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"LANDING COPY UNSIGNED", resp.content)
        self.assertIn(b"A research project of Dr Rosie Graham", resp.content)
        self.assertIn(b"An alpha release of the interface is featured", resp.content)
        self.assertIn(b"Read the full privacy notice", resp.content)
        self.assertNotIn(b"Coming Soon", resp.content)
        self.assertNotIn(b"Ethical Interface", resp.content)

    def test_cookies_copy_covers_anonymous_and_signed_in_use(self):
        resp = self.client.get("/cookies/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"This site sets no cookies when you browse it.", resp.content)
        self.assertIn(b"If you sign in to the project dashboard", resp.content)
        self.assertNotIn(b"birmingham.ac.uk/privacy/cookies", resp.content)
        self.assertNotIn(b"Ethical Interface website does not use cookies", resp.content)


class WorkshopPrivacyPageQueryTests(TestCase):
    def test_public_gets_make_zero_queries(self):
        for path, _template in PUBLIC_PAGES:
            with self.subTest(path=path):
                with CaptureQueriesContext(connection) as ctx:
                    resp = self.client.get(path)
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(len(ctx.captured_queries), 0)

    def test_inverted_zero_query_check_fails_when_setting_is_read(self):
        with CaptureQueriesContext(connection) as ctx:
            list(Setting.objects.all()[:1])
        self.assertGreater(len(ctx.captured_queries), 0)
        with self.assertRaises(AssertionError):
            self.assertEqual(len(ctx.captured_queries), 0)


class WorkshopPrivacyThirdPartyInvertedTests(SimpleTestCase):
    def test_scanner_catches_planted_third_party_script(self):
        planted = '<script src="https://cdn.example.com/tracker.js"></script>'
        hits = third_party_hits(planted)
        self.assertTrue(hits)
        with self.assertRaises(AssertionError):
            self.assertEqual(hits, [])

    def test_scanner_catches_planted_google_font(self):
        planted = '<link href="https://fonts.googleapis.com/css?family=Roboto:700,900" rel="stylesheet">'
        hits = third_party_hits(planted)
        self.assertTrue(hits)
        with self.assertRaises(AssertionError):
            self.assertEqual(hits, [])

    def test_existing_base_template_is_the_positive_third_party_case(self):
        """base.html loads Google Fonts. The scanner must flag it, then we keep it off public pages."""
        html = BASE_TEMPLATE.read_text(encoding="utf-8")
        hits = third_party_hits(html)
        self.assertTrue(hits)
        self.assertTrue(any("fonts.googleapis.com" in hit for hit in hits))
        for template_name in ("general/home.html", "general/privacy.html", "general/cookies.html"):
            with self.subTest(template=template_name):
                rendered = render_to_string(template_name)
                self.assertEqual(third_party_hits(rendered), [])
