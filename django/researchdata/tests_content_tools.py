"""Tests for content package apply and the content tools admin page."""

import json
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from . import models
from .classifier_config import get_classifier_threshold, set_classifier_threshold
from .content_tools_views import _package_digest, _parse_spot_check_line
from .services.content_apply import apply_package, count_unapproves, validate_package

User = get_user_model()
REPO_ROOT = Path(__file__).resolve().parents[2]

MINIMAL_PACKAGE = {
    "groups": [{"name": "Tools", "admin_notes": "test group"}],
    "topics": [
        {
            "name": "Surveillance capitalism",
            "group": "Tools",
            "description": "This content covers surveillance capitalism and data extraction.",
            "example_queries": ["what is surveillance capitalism"],
        }
    ],
    "prompts": [
        {
            "ref": "ISI-T-001",
            "topic": "Surveillance capitalism",
            "style": "reflective",
            "prompt_content": "Reflect on how your data is collected.",
            "priority": 10,
            "admin_approved": True,
            "admin_notes": "ref:ISI-T-001 | set 4",
            "triggers": ["surveillance capitalism"],
        }
    ],
}


def make_staff_client():
    user = User.objects.create_user(
        username="admin@test.com",
        email="admin@test.com",
        password="secret",
    )
    client = Client()
    client.force_login(user)
    return client


class ClassifierThresholdCacheTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_repeated_gets_hit_database_once(self):
        set_classifier_threshold(0.41)
        cache.clear()
        # First call reads Setting; subsequent calls within TTL must not.
        with self.assertNumQueries(1):
            first = get_classifier_threshold()
            second = get_classifier_threshold()
            third = get_classifier_threshold()
        self.assertEqual(first, 0.41)
        self.assertEqual(second, 0.41)
        self.assertEqual(third, 0.41)

    def test_set_invalidates_cache_immediately(self):
        set_classifier_threshold(0.41)
        self.assertEqual(get_classifier_threshold(), 0.41)
        set_classifier_threshold(0.55)
        self.assertEqual(get_classifier_threshold(), 0.55)


class SpotCheckParseTests(TestCase):
    def test_blank_and_hash_only_are_skipped(self):
        self.assertEqual(_parse_spot_check_line(""), ("", False))
        self.assertEqual(_parse_spot_check_line("   "), ("", False))
        self.assertEqual(_parse_spot_check_line("#"), ("", False))
        self.assertEqual(_parse_spot_check_line("#neg"), ("", False))
        self.assertEqual(_parse_spot_check_line("#neg "), ("", False))

    def test_neg_with_query(self):
        self.assertEqual(_parse_spot_check_line("#neg weather tomorrow"), ("weather tomorrow", True))

    def test_fixture_parses_to_fifty_one_with_five_negatives(self):
        # The review brief claimed 50; the checked-in file has 51 query lines
        # (46 ordinary + 5 #neg). Assert the real count so a silent drop is caught.
        path = REPO_ROOT / "data" / "spot-check-queries-2026-07-09.txt"
        if not path.exists():
            path = Path(__file__).resolve().parent / "fixtures" / "spot-check-queries-2026-07-09.txt"
        text = path.read_text(encoding="utf-8")
        rows = []
        for line in text.splitlines():
            query, negative = _parse_spot_check_line(line)
            if query:
                rows.append((query, negative))
        self.assertEqual(len(rows), 51)
        self.assertEqual(sum(1 for _, negative in rows if negative), 5)


class ApplyPackageTests(TestCase):
    def test_dry_run_writes_nothing(self):
        before_groups = models.TopicGroup.objects.count()
        before_topics = models.Topic.objects.count()
        before_prompts = models.Prompt.objects.count()
        result = apply_package(MINIMAL_PACKAGE, dry_run=True)
        self.assertEqual(models.TopicGroup.objects.count(), before_groups)
        self.assertEqual(models.Topic.objects.count(), before_topics)
        self.assertEqual(models.Prompt.objects.count(), before_prompts)
        self.assertGreater(result.created["topics"], 0)

    def test_apply_is_idempotent(self):
        first = apply_package(MINIMAL_PACKAGE, dry_run=False)
        second = apply_package(MINIMAL_PACKAGE, dry_run=False)
        self.assertEqual(first.created["topics"], 1)
        self.assertEqual(second.created["topics"], 0)
        self.assertEqual(second.updated["prompts"], 1)
        self.assertEqual(models.Topic.objects.filter(name="Surveillance capitalism").count(), 1)

    def test_prompt_content_change_reported(self):
        apply_package(MINIMAL_PACKAGE, dry_run=False)
        changed = dict(MINIMAL_PACKAGE)
        changed["prompts"] = [dict(MINIMAL_PACKAGE["prompts"][0])]
        changed["prompts"][0]["prompt_content"] = "Updated reflective prompt text."
        result = apply_package(changed, dry_run=True)
        content_changes = [
            c for c in result.changes if c[0] == "prompt" and c[2] == "prompt_content"
        ]
        self.assertEqual(len(content_changes), 1)
        self.assertEqual(content_changes[0][3], "Reflect on how your data is collected.")
        self.assertEqual(content_changes[0][4], "Updated reflective prompt text.")

    def test_unapprove_reported(self):
        apply_package(MINIMAL_PACKAGE, dry_run=False)
        unapproved = dict(MINIMAL_PACKAGE)
        unapproved["prompts"] = [dict(MINIMAL_PACKAGE["prompts"][0])]
        unapproved["prompts"][0]["admin_approved"] = False
        self.assertEqual(count_unapproves(unapproved), 1)
        result = apply_package(unapproved, dry_run=True)
        approval_changes = [
            c for c in result.changes if c[0] == "prompt" and c[2] == "admin_approved"
        ]
        self.assertTrue(any(c[3] is True and c[4] is False for c in approval_changes))

    def test_malformed_package_rejected(self):
        bad = {"groups": [], "topics": [], "prompts": [{"ref": "X"}]}
        with self.assertRaises(ValidationError):
            validate_package(bad)
        before = models.Topic.objects.count()
        with self.assertRaises(ValidationError):
            apply_package(bad, dry_run=True)
        self.assertEqual(models.Topic.objects.count(), before)

    def test_orphan_topic_group_rejected_before_write(self):
        bad = {
            "groups": [{"name": "OnlyGroup"}],
            "topics": [
                {
                    "name": "Orphan topic",
                    "group": "MissingGroup",
                    "description": "desc",
                    "example_queries": [],
                }
            ],
            "prompts": [],
        }
        with self.assertRaises(ValidationError) as ctx:
            validate_package(bad)
        self.assertIn("unknown group", str(ctx.exception))
        before = models.TopicGroup.objects.count()
        with self.assertRaises(ValidationError):
            apply_package(bad, dry_run=False)
        self.assertEqual(models.TopicGroup.objects.count(), before)

    def test_orphan_prompt_topic_rejected_before_write(self):
        bad = {
            "groups": [{"name": "Tools"}],
            "topics": [
                {
                    "name": "Surveillance capitalism",
                    "group": "Tools",
                    "description": "desc",
                    "example_queries": [],
                }
            ],
            "prompts": [
                {
                    "ref": "ISI-X-001",
                    "topic": "Not In Package",
                    "style": "reflective",
                    "prompt_content": "x",
                    "admin_approved": True,
                }
            ],
        }
        with self.assertRaises(ValidationError) as ctx:
            validate_package(bad)
        self.assertIn("unknown topic", str(ctx.exception))
        before_prompts = models.Prompt.objects.count()
        with self.assertRaises(ValidationError):
            apply_package(bad, dry_run=False)
        self.assertEqual(models.Prompt.objects.count(), before_prompts)

    def test_count_unapproves_uses_database_state(self):
        apply_package(MINIMAL_PACKAGE, dry_run=False)
        models.Prompt.objects.filter(admin_notes__icontains="ref:ISI-T-001 ").update(
            admin_approved=True
        )
        unapproved = dict(MINIMAL_PACKAGE)
        unapproved["prompts"] = [dict(MINIMAL_PACKAGE["prompts"][0])]
        unapproved["prompts"][0]["admin_approved"] = False
        self.assertEqual(count_unapproves(unapproved), 1)
        models.Prompt.objects.filter(admin_notes__icontains="ref:ISI-T-001 ").update(
            admin_approved=False
        )
        # Already unapproved in DB: package alone says False, but no transition.
        self.assertEqual(count_unapproves(unapproved), 0)


class ContentToolsViewTests(TestCase):
    def test_non_staff_gets_403(self):
        user = User.objects.create_user(
            username="nostaff@test.com",
            email="nostaff@test.com",
            password="secret",
        )
        User.objects.filter(pk=user.pk).update(is_staff=True, is_superuser=False)
        client = Client()
        client.force_login(user)
        resp = client.get(reverse("content-tools"))
        self.assertEqual(resp.status_code, 403)

    def test_upload_invalid_json_rejected(self):
        client = make_staff_client()
        before = models.ContentApply.objects.count()
        resp = client.post(
            reverse("content-tools"),
            {
                "action": "upload_preview",
                "package": SimpleUploadedFile("bad.json", b"not json", content_type="application/json"),
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(models.ContentApply.objects.count(), before)

    def test_upload_preview_then_apply(self):
        client = make_staff_client()
        payload = json.dumps(MINIMAL_PACKAGE).encode("utf-8")
        resp = client.post(
            reverse("content-tools"),
            {
                "action": "upload_preview",
                "package": SimpleUploadedFile(
                    "pkg.json", payload, content_type="application/json"
                ),
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(models.ContentApply.objects.filter(dry_run=True).exists())
        digest = client.session["content_tools_package_digest"]
        self.assertEqual(digest, _package_digest(MINIMAL_PACKAGE))
        resp = client.post(
            reverse("content-tools"),
            {"action": "apply", "package_digest": digest},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(models.Topic.objects.filter(name="Surveillance capitalism").exists())
        self.assertTrue(models.ContentApply.objects.filter(dry_run=False).exists())

    def test_apply_rejects_stale_digest(self):
        client = make_staff_client()
        payload = json.dumps(MINIMAL_PACKAGE).encode("utf-8")
        client.post(
            reverse("content-tools"),
            {
                "action": "upload_preview",
                "package": SimpleUploadedFile(
                    "pkg.json", payload, content_type="application/json"
                ),
            },
        )
        before = models.Topic.objects.filter(name="Surveillance capitalism").count()
        resp = client.post(
            reverse("content-tools"),
            {"action": "apply", "package_digest": "0" * 64},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            models.Topic.objects.filter(name="Surveillance capitalism").count(), before
        )
    @override_settings(CLASSIFIER_ENABLED=True)
    def test_set_threshold_persists(self):
        client = make_staff_client()
        resp = client.post(
            reverse("content-tools"),
            {"action": "set_threshold", "classifier_threshold": "0.42"},
        )
        self.assertEqual(resp.status_code, 302)
        row = models.Setting.objects.get(key="CLASSIFIER_THRESHOLD")
        self.assertEqual(row.value, "0.42")

    @override_settings(CLASSIFIER_ENABLED=True)
    def test_spot_check_renders_without_model(self):
        from researchdata.embedding import ClassifierUnavailable

        client = make_staff_client()
        with mock.patch(
            "researchdata.content_tools_views.classify_query",
            side_effect=ClassifierUnavailable("no model"),
        ):
            resp = client.post(
                reverse("content-tools"),
                {"action": "spot_check", "spot_check_queries": "test query\n"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Classifier unavailable")
