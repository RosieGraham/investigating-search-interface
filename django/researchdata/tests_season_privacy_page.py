"""
Failing contract: public workshop pages are anonymous, query-free, and have
no third-party scripts or fonts. Inverted controls plant the positive case
for each negative assurance.
"""

import re
from pathlib import Path

from django.db import connection
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings
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
THIRD_PARTY_RESOURCE = re.compile(
    r"""<(?:link|script|img|iframe)\b[^>]*?(?:src|href)\s*=\s*["']((?:https?:)?//[^"']+)["']""",
    re.IGNORECASE | re.DOTALL,
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


def third_party_resource_hits(html):
    """Remote hosts loaded by link/script/img/iframe. Footer anchors are not loads."""
    hits = []
    for url in THIRD_PARTY_RESOURCE.findall(html):
        lowered = url.lower()
        if any(host in lowered for host in LOCAL_HOSTS):
            continue
        hits.append(url)
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
                self.assertNotIn("cookiesmsg", html)

    @override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False)
    def test_404_has_no_third_party_host_or_cookie_banner(self):
        """Production 404 extends base.html. DEBUG=True would hide that behind Django's technical 404."""
        resp = self.client.get("/this-path-does-not-exist/")
        self.assertEqual(resp.status_code, 404)
        html = resp.content.decode("utf-8")
        self.assertIn("error code: 404", html)
        self.assertIn("birmingham.ac.uk", html)
        self.assertNotIn("fonts.googleapis.com", html)
        self.assertNotIn("cookiesmsg", html)
        self.assertEqual(third_party_resource_hits(html), [])
        self.assertEqual(list(resp.cookies.keys()), [])


OLD_BRAND = "Ethical Interface"
SKIP_DIR_NAMES = {".git", "__pycache__", ".venv", "venv", "node_modules", "migrations"}


def _is_excluded_python(path):
    if any(part in SKIP_DIR_NAMES for part in path.parts):
        return True
    name = path.name
    if name.startswith("test_") or name.startswith("tests_"):
        return True
    if name.endswith("_test.py") or name == "tests.py":
        return True
    return False


def old_brand_source_hits():
    """Paths under the repo whose templates or non-test Python still name the old brand."""
    hits = []
    django_root = REPO_ROOT / "django"
    for path in django_root.rglob("*.html"):
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if OLD_BRAND in path.read_text(encoding="utf-8"):
            hits.append(str(path.relative_to(REPO_ROOT)))
    for path in REPO_ROOT.rglob("*.py"):
        if _is_excluded_python(path):
            continue
        if OLD_BRAND in path.read_text(encoding="utf-8"):
            hits.append(str(path.relative_to(REPO_ROOT)))
    return hits


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

    def test_old_brand_is_absent_from_templates_and_python_source(self):
        hits = old_brand_source_hits()
        self.assertEqual(
            hits,
            [],
            "pre-rebrand name still present in: " + ", ".join(hits),
        )


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

    def test_cookie_banner_assertion_catches_hashed_filename(self):
        planted = "/static/js/cookiesmsg.7c86252baa0a.js"
        with self.assertRaises(AssertionError):
            self.assertNotIn("cookiesmsg", planted)

    def test_base_template_has_no_third_party_fonts_or_cookie_banner(self):
        """Error pages extend base.html. It must not load Google Fonts or cookiesmsg."""
        html = BASE_TEMPLATE.read_text(encoding="utf-8")
        self.assertNotIn("fonts.googleapis.com", html)
        self.assertNotIn("cookiesmsg", html)
        self.assertEqual(third_party_resource_hits(html), [])
        self.assertIn("birmingham.ac.uk", html)
        for template_name in ("general/home.html", "general/privacy.html", "general/cookies.html"):
            with self.subTest(template=template_name):
                rendered = render_to_string(template_name)
                self.assertEqual(third_party_hits(rendered), [])
