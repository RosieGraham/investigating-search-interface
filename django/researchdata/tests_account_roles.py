"""Tests for the July 2026 account-roles change.

Until July 2026, account.User.save() forced is_staff and is_superuser True
on every save, so every account was a superuser and participant data sat
behind any login. These tests pin the new model:

- User.save() no longer touches privilege flags; the email-to-username
  rewrite (login by email) is unchanged.
- Staff editors (is_staff=True, is_superuser=False) can log into the
  dashboard, view content read-only, read the apply/evaluation histories,
  and run the content tools READ actions (spot check, evaluation).
- Staff editors cannot write content, cannot change the decision rule,
  cannot reach participant data (EngagementEvent, Response,
  NotRelevantReport), the Setting table, DataInsert, or user management.
- Superusers are unchanged, and ensure_superuser still yields one.
"""

import io
from unittest import mock

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from . import models

User = get_user_model()


def make_user(email, *, staff=False, superuser=False, password="pw-test-1234"):
    user = User.objects.create_user(
        username=email, email=email, password=password,
        is_staff=staff, is_superuser=superuser,
    )
    return user


def logged_in_client(user):
    client = Client()
    client.force_login(user)
    return client


class UserModelRolesTests(TestCase):
    def test_save_no_longer_forces_privileges(self):
        user = make_user("plain@test.com")
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        # A second save must not escalate either.
        user.first_name = "Plain"
        user.save()
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_staff_flag_survives_save_without_escalation(self):
        user = make_user("staff@test.com", staff=True)
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_superuser_flags_are_kept(self):
        user = make_user("root@test.com", staff=True, superuser=True)
        user.save()
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_email_to_username_rewrite_unchanged(self):
        user = User(email="  Mixed.Case@Uni.AC.UK ")
        user.set_password("pw-test-1234")
        user.save()
        user.refresh_from_db()
        self.assertEqual(user.email, "mixed.case@uni.ac.uk")
        self.assertEqual(user.username, "mixed.case@uni.ac.uk")

    def test_login_by_email_case_insensitive(self):
        make_user("staff2@test.com", staff=True, password="pw-test-1234")
        client = Client()
        self.assertTrue(client.login(username="STAFF2@Test.com", password="pw-test-1234"))

    def test_ensure_superuser_still_yields_working_superuser(self):
        env = {
            "DJANGO_SUPERUSER_USERNAME": "boot@test.com",
            "DJANGO_SUPERUSER_PASSWORD": "pw-boot-9999",
            "DJANGO_SUPERUSER_EMAIL": "boot@test.com",
        }
        with mock.patch.dict("os.environ", env):
            call_command("ensure_superuser", stdout=io.StringIO())
        user = User.objects.get(username="boot@test.com")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_active)
        client = Client()
        self.assertTrue(client.login(username="boot@test.com", password="pw-boot-9999"))

    def test_audit_accounts_reports_roles_and_writes_nothing(self):
        make_user("root2@test.com", staff=True, superuser=True)
        make_user("editor@test.com", staff=True)
        make_user("nobody@test.com")
        before = list(User.objects.values_list("id", "is_staff", "is_superuser"))
        out = io.StringIO()
        call_command("audit_accounts", stdout=out)
        text = out.getvalue()
        self.assertIn("root2@test.com", text)
        self.assertIn("superuser", text)
        self.assertIn("staff-editor", text)
        self.assertIn("no-dashboard", text)
        self.assertEqual(before, list(User.objects.values_list("id", "is_staff", "is_superuser")))


class AdminAccessMatrixTests(TestCase):
    """Staff editors: content read-only; participant data invisible."""

    @classmethod
    def setUpTestData(cls):
        cls.group = models.TopicGroup.objects.create(name="Tools")
        cls.topic = models.Topic.objects.create(
            name="Surveillance capitalism", topic_group=cls.group,
            description="Data extraction as a business model.",
        )
        cls.prompt = models.Prompt.objects.create(
            topic=cls.topic, prompt_content="Reflect on how your data is collected.",
            admin_approved=True,
        )
        cls.staff = make_user("editor@test.com", staff=True)
        cls.root = make_user("root@test.com", staff=True, superuser=True)

    def test_staff_can_open_admin_index(self):
        resp = logged_in_client(self.staff).get(reverse("admin:index"))
        self.assertEqual(resp.status_code, 200)

    def test_staff_can_view_content_changelists(self):
        client = logged_in_client(self.staff)
        for url_name in (
            "admin:researchdata_topicgroup_changelist",
            "admin:researchdata_topic_changelist",
            "admin:researchdata_trigger_changelist",
            "admin:researchdata_prompt_changelist",
            "admin:researchdata_contentapply_changelist",
            "admin:researchdata_matchingevaluation_changelist",
        ):
            with self.subTest(url=url_name):
                self.assertEqual(client.get(reverse(url_name)).status_code, 200)

    def test_staff_cannot_see_participant_or_system_tables(self):
        client = logged_in_client(self.staff)
        for url_name in (
            "admin:researchdata_engagementevent_changelist",
            "admin:researchdata_response_changelist",
            "admin:researchdata_notrelevantreport_changelist",
            "admin:researchdata_setting_changelist",
            "admin:researchdata_datainsert_changelist",
            "admin:account_user_changelist",
        ):
            with self.subTest(url=url_name):
                self.assertEqual(client.get(reverse(url_name)).status_code, 403)

    def test_staff_cannot_add_or_edit_content(self):
        client = logged_in_client(self.staff)
        self.assertEqual(
            client.get(reverse("admin:researchdata_topic_add")).status_code, 403)
        change_url = reverse("admin:researchdata_prompt_change", args=[self.prompt.pk])
        before = self.prompt.prompt_content
        resp = client.post(change_url, {"prompt_content": "tampered"})
        self.assertEqual(resp.status_code, 403)
        self.prompt.refresh_from_db()
        self.assertEqual(self.prompt.prompt_content, before)

    def test_staff_lose_approve_unapprove_actions(self):
        request = RequestFactory().get("/admin/researchdata/prompt/")
        prompt_admin = django_admin.site._registry[models.Prompt]
        request.user = self.staff
        staff_actions = prompt_admin.get_actions(request)
        self.assertNotIn("approve", staff_actions)
        self.assertNotIn("unapprove", staff_actions)
        request.user = self.root
        root_actions = prompt_admin.get_actions(request)
        self.assertIn("approve", root_actions)
        self.assertIn("unapprove", root_actions)

    def test_superuser_still_sees_everything(self):
        client = logged_in_client(self.root)
        for url_name in (
            "admin:researchdata_topic_changelist",
            "admin:researchdata_engagementevent_changelist",
            "admin:researchdata_response_changelist",
            "admin:researchdata_notrelevantreport_changelist",
            "admin:researchdata_setting_changelist",
            "admin:account_user_changelist",
        ):
            with self.subTest(url=url_name):
                self.assertEqual(client.get(reverse(url_name)).status_code, 200)
        self.assertEqual(
            client.get(reverse("admin:researchdata_topic_add")).status_code, 200)


class ContentToolsRolesTests(TestCase):
    """Per-action gates: staff get the READ actions, superusers everything."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = make_user("editor@test.com", staff=True)
        cls.root = make_user("root@test.com", staff=True, superuser=True)

    def test_staff_get_page_read_only(self):
        resp = logged_in_client(self.staff).get(reverse("content-tools"))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["can_manage_content"])
        content = resp.content.decode()
        self.assertNotIn("Upload and apply", content)
        self.assertNotIn("Save threshold", content)
        self.assertIn("Run spot check", content)
        self.assertIn("Run evaluation", content)

    def test_superuser_get_page_with_write_forms(self):
        resp = logged_in_client(self.root).get(reverse("content-tools"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["can_manage_content"])
        self.assertIn("Upload and apply", resp.content.decode())

    def test_staff_can_run_spot_check(self):
        from researchdata.embedding import ClassifierUnavailable
        client = logged_in_client(self.staff)
        with mock.patch(
            "researchdata.content_tools_views.classify_query",
            side_effect=ClassifierUnavailable("no model"),
        ):
            resp = client.post(
                reverse("content-tools"),
                {"action": "spot_check", "spot_check_queries": "test query\n"},
            )
        self.assertEqual(resp.status_code, 200)

    def test_staff_can_run_evaluation_action(self):
        # Without the model this ends in a friendly error redirect; the point
        # is that it is ALLOWED (not 403): evaluation is a read action.
        resp = logged_in_client(self.staff).post(
            reverse("content-tools"), {"action": "run_evaluation"})
        self.assertIn(resp.status_code, (200, 302))

    def test_staff_blocked_from_write_actions(self):
        client = logged_in_client(self.staff)
        cases = [
            ({"action": "set_threshold", "classifier_threshold": "0.99"}, None),
            ({"action": "apply", "package_digest": "0" * 64}, None),
            ({"action": "rebuild_index"}, None),
        ]
        for payload, _ in cases:
            with self.subTest(action=payload["action"]):
                resp = client.post(reverse("content-tools"), payload)
                self.assertEqual(resp.status_code, 403)
        self.assertFalse(
            models.Setting.objects.filter(key="CLASSIFIER_THRESHOLD", value="0.99").exists())

    def test_staff_blocked_from_upload_even_without_action_field(self):
        # A bare file POST is inferred as upload_preview; the gate must catch it.
        client = logged_in_client(self.staff)
        before = models.ContentApply.objects.count()
        resp = client.post(
            reverse("content-tools"),
            {"package": SimpleUploadedFile(
                "pkg.json", b"{}", content_type="application/json")},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(models.ContentApply.objects.count(), before)

    def test_superuser_can_still_set_threshold(self):
        resp = logged_in_client(self.root).post(
            reverse("content-tools"),
            {"action": "set_threshold", "classifier_threshold": "0.42"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            models.Setting.objects.filter(key="CLASSIFIER_THRESHOLD", value="0.42").exists())
