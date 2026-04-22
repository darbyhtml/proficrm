"""W2.1.3b — Verify Group D codification preserves existing behavior.

All 4 endpoints have been decorated с @policy_required. Tests verify:
- Behavior unchanged для both qa-manager-like role и admin role.
- Defense-in-depth preserved: inline permission checks remain active.
- F3 IDOR-fix preserved для task_add_comment.
- `_v2_load_task_for_user` role-based visibility preserved.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from accounts.models import Branch
from companies.models import Company
from tasksapp.models import Task

User = get_user_model()


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class W2GroupDCodificationTests(TestCase):
    """Verify 4 Group D endpoints work identically после @policy_required addition."""

    def setUp(self):
        self.branch_ekb = Branch.objects.create(code="w2_ekb", name="W2 EKB")
        self.branch_krd = Branch.objects.create(code="w2_krd", name="W2 Krasnodar")

        self.admin = User.objects.create_superuser(
            username="w2_admin",
            email="a@w2.ru",
            password="pw",
        )
        # create_superuser не сетит role → ставим явно (inline checks в
        # _can_edit_task_ui / _v2_load_task_for_user используют role check,
        # не is_superuser, для branch-wide visibility).
        self.admin.role = User.Role.ADMIN
        self.admin.save(update_fields=["role"])
        self.manager = User.objects.create_user(
            username="w2_mgr",
            email="m@w2.ru",
            password="pw",
            role=User.Role.MANAGER,
            branch=self.branch_ekb,
        )

        # Own company + task (visible to manager)
        self.own_company = Company.objects.create(
            name="w2_own_co",
            branch=self.branch_ekb,
            responsible=self.manager,
        )
        self.own_task = Task.objects.create(
            title="w2_own_task",
            assigned_to=self.manager,
            company=self.own_company,
        )

        # Other branch (NOT visible to manager)
        self.other_company = Company.objects.create(
            name="w2_other_co",
            branch=self.branch_krd,
        )
        self.other_task = Task.objects.create(
            title="w2_other_task",
            company=self.other_company,
        )

    # --- Endpoint #1: analytics_v2_home ---

    def test_analytics_v2_accessible_to_manager(self):
        c = Client()
        c.force_login(self.manager)
        r = c.get("/analytics/v2/")
        self.assertEqual(r.status_code, 200, "Manager should see /analytics/v2/")

    def test_analytics_v2_accessible_to_admin(self):
        c = Client()
        c.force_login(self.admin)
        r = c.get("/analytics/v2/")
        self.assertEqual(r.status_code, 200, "Admin should see /analytics/v2/")

    # --- Endpoint #2: messenger_agent_status ---
    # settings_test default имеет MESSENGER_ENABLED=False — view raises Http404
    # через `ensure_messenger_enabled_view()` helper. Staging .env задаёт =1.
    # Override нужен для CI parity со staging runtime behavior.

    @override_settings(MESSENGER_ENABLED=True)
    def test_messenger_agent_status_self_service_redirect(self):
        """POST updates own AgentProfile, redirects to messenger."""
        c = Client()
        c.force_login(self.manager)
        r = c.post("/messenger/me/status/", {"status": "online", "next": "/"})
        self.assertEqual(r.status_code, 302, "Expected redirect after status update")

    @override_settings(MESSENGER_ENABLED=True)
    def test_messenger_agent_status_get_redirects(self):
        """GET (wrong method) redirects to conversations."""
        c = Client()
        c.force_login(self.manager)
        r = c.get("/messenger/me/status/")
        self.assertEqual(r.status_code, 302)

    # --- Endpoint #3: task_add_comment — F3 IDOR-fix preserved ---

    def test_task_comment_own_task_allowed(self):
        c = Client()
        c.force_login(self.manager)
        r = c.post(
            f"/tasks/{self.own_task.id}/comment/",
            {"text": "w2_test_comment"},
        )
        self.assertEqual(r.status_code, 200, "Manager can comment on own task")

    def test_task_comment_idor_preserved_for_other_branch_task(self):
        """F3 IDOR-fix: manager cannot comment on invisible task — expect 404."""
        c = Client()
        c.force_login(self.manager)
        r = c.post(
            f"/tasks/{self.other_task.id}/comment/",
            {"text": "idor_probe"},
        )
        self.assertEqual(
            r.status_code,
            404,
            "IDOR-fix must return 404, not 403 (does not leak existence)",
        )

    def test_task_comment_admin_can_comment_anywhere(self):
        c = Client()
        c.force_login(self.admin)
        r = c.post(
            f"/tasks/{self.other_task.id}/comment/",
            {"text": "admin_comment"},
        )
        self.assertEqual(r.status_code, 200)

    # --- Endpoint #4: task_view_v2_partial — _v2_load_task_for_user preserved ---

    def test_task_v2_partial_own_task_allowed(self):
        c = Client()
        c.force_login(self.manager)
        r = c.get(f"/tasks/v2/{self.own_task.id}/partial/")
        self.assertEqual(r.status_code, 200)

    def test_task_v2_partial_other_branch_denied(self):
        """_v2_load_task_for_user raises PermissionDenied для cross-branch access."""
        c = Client()
        c.force_login(self.manager)
        r = c.get(f"/tasks/v2/{self.other_task.id}/partial/")
        self.assertEqual(
            r.status_code,
            403,
            "_v2_load_task_for_user должен raise PermissionDenied для cross-branch",
        )

    def test_task_v2_partial_admin_sees_any(self):
        c = Client()
        c.force_login(self.admin)
        r = c.get(f"/tasks/v2/{self.other_task.id}/partial/")
        self.assertEqual(r.status_code, 200)
