"""Tests for content package apply and the content tools admin page."""

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from . import models
from .services.content_apply import apply_package, count_unapproves, validate_package

User = get_user_model()

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
        resp = client.post(reverse("content-tools"), {"action": "apply"})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(models.Topic.objects.filter(name="Surveillance capitalism").exists())
        self.assertTrue(models.ContentApply.objects.filter(dry_run=False).exists())

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
